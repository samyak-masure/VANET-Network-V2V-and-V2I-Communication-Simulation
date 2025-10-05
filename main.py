import simpy
import yaml
import random
from pathlib import Path
from statistics import mean
import queue

# ------------ Config (Now returns None on failure) ------------
def load_config(path=None):
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "scenario_simple.yaml"
    
    try:
        with open(path, "r") as f:
            print(f"Loading configuration from: {path}")
            return yaml.safe_load(f)
    except FileNotFoundError:
        # Instead of exiting, we return the error information
        error_info = {
            "type": "FATAL_ERROR",
            "message": "Could not find config file!",
            "path": str(path)
        }
        return error_info

# ------------ Helper Functions ------------
def in_rsu_zone(pos, rsu_start, rsu_range):
    return rsu_start <= pos <= rsu_start + rsu_range

def prune_messages(msgs, now, ttl):
    cutoff = now - ttl
    i = 0
    while i < len(msgs):
        if msgs[i]["time"] < cutoff:
            msgs.pop(i)
        else:
            i += 1

# ------------ Vehicle (AODV-style reactive V2V) ------------
def vehicle(env, vid, config, rsu_data_for_vehicles, # Changed this to pass RSU-specific data
            wait_log, v2v_messages, v2r_inbox, vehicles_state, log_queue,
            rsu_ack_inbox): # Removed rsu_broadcast_channel here
    
    speed = random.randint(config["vehicles"]["min_speed"], config["vehicles"]["max_speed"])
    v2v_range = config["vehicles"]["v2v_range"]
    v2v_ttl   = config["vehicles"].get("v2v_message_ttl", 5)
    wait_prob = config["vehicles"]["intersection_wait_prob"]
    wait_min  = config["vehicles"]["intersection_wait_min"]
    wait_max  = config["vehicles"]["intersection_wait_max"]
    intersections = sorted(config["intersections"]["positions"])
    global_rsu_check_interval = config["vehicles"].get("global_rsu_check_interval", config["time_step"])

    pos = 0.0
    total_wait = 0
    
    # NEW: Store last processed broadcast time per RSU for this vehicle
    last_processed_rsu_broadcast_time = {} 
    
    while env.now < config["simulation_time"]:
        # Determine RSU range for the RSU this vehicle might be interacting with
        # This assumes a single RSU or that all RSUs have the same range for global broadcast logic
        # For multiple RSUs, this would need to iterate through rsu_data_for_vehicles
        # Here we simplify, assuming rsu_data_for_vehicles contains info for the relevant RSU
        rsu_start_pos = rsu_data_for_vehicles[0]["position"] # Assuming rsu_data_for_vehicles is a list, get first RSU's info
        rsu_coverage_range = rsu_data_for_vehicles[0]["range"] # Assuming rsu_data_for_vehicles is a list, get first RSU's info

        inside_rsu_broadcast_range = in_rsu_zone(pos, rsu_start_pos, rsu_coverage_range)
        
        next_pos = pos + speed
        crossing = None
        for x in intersections:
            if pos < x <= next_pos:
                crossing = x
                break

        if crossing is not None:
            pos = float(crossing)
            vehicles_state[vid] = {"pos": pos, "speed": 0}

            if random.random() < wait_prob:
                wait_time = random.randint(wait_min, wait_max)
                log_queue.put({
                    "type": "VEHICLE_WAIT_START", "time": env.now, "vid": vid, 
                    "pos": pos, "wait_time": wait_time
                })
                v2v_messages.append({
                    "type": "WAIT_START", "from": vid, "time": env.now, "pos": pos, "wait": total_wait + wait_time
                })
                # Check if in RSU's direct V2R range (usually smaller than global broadcast range)
                if in_rsu_zone(pos, rsu_start_pos, rsu_data_for_vehicles[0]["range"]): # Using RSU's specific V2R range
                    v2r_inbox.append({
                        "type": "WAIT_UPDATE", "from": vid, "time": env.now, "pos": pos, "wait": total_wait + wait_time
                    })
                total_wait += wait_time
                yield env.timeout(wait_time)
                v2v_messages.append({
                    "type": "WAIT_END", "from": vid, "time": env.now, "pos": pos, "wait": total_wait
                })
                # Check if in RSU's direct V2R range
                if in_rsu_zone(pos, rsu_start_pos, rsu_data_for_vehicles[0]["range"]): # Using RSU's specific V2R range
                    v2r_inbox.append({
                        "type": "WAIT_UPDATE", "from": vid, "time": env.now, "pos": pos, "wait": total_wait
                    })
        pos += speed
        vehicles_state[vid] = {"pos": pos, "speed": speed}
        log_queue.put({"type": "VEHICLE_MOVE", "time": env.now, "vid": vid, "pos": pos})
        
        prune_messages(v2v_messages, env.now, v2v_ttl) # Prune V2V messages
        
        # V2V Receive Logic
        for m in v2v_messages:
            if m["from"] == vid:
                continue
            if abs(pos - m["pos"]) <= v2v_range:
                status = "free" if m["wait"] == 0 else f"delayed {m['wait']}s"
                log_queue.put({
                    "type": "V2V_RECEIVE", "time": env.now, "from": m["from"], 
                    "to": vid, "msg_type": m["type"], "status": status
                })
        
        # RSU Zone Entry/Exit (V2R - direct communication with RSU)
        # This `inside_rsu` flag refers to the direct RSU communication range, not the wider broadcast range
        # We need a per-RSU 'inside' status if there are multiple RSUs
        # For simplicity, and current single RSU, this still works for direct V2R
        is_in_direct_rsu_zone = in_rsu_zone(pos, rsu_start_pos, rsu_data_for_vehicles[0]["range"]) # Direct RSU range
        
        # This part of the vehicle logic deals with *direct* V2R messages, not global broadcasts
        # The 'inside_rsu' logic is specific to direct V2R, which has its own range
        # The 'inside_rsu' state needs to be maintained per RSU if there are multiple RSUs.
        # For a single RSU, it simplifies. Let's make it consistent.
        if not hasattr(env, 'vehicle_direct_rsu_status'):
            env.vehicle_direct_rsu_status = {}
        
        if vid not in env.vehicle_direct_rsu_status:
             env.vehicle_direct_rsu_status[vid] = False # Not inside any RSU direct range initially

        if is_in_direct_rsu_zone and not env.vehicle_direct_rsu_status[vid]:
            env.vehicle_direct_rsu_status[vid] = True
            log_queue.put({"type": "RSU_ENTER", "time": env.now, "vid": vid, "pos": pos, "rid": rsu_data_for_vehicles[0]["id"]})
            v2r_inbox.append({"type": "HELLO", "from": vid, "time": env.now, "pos": pos, "wait": total_wait, "to_rid": rsu_data_for_vehicles[0]["id"]})
        elif not is_in_direct_rsu_zone and env.vehicle_direct_rsu_status[vid]:
            env.vehicle_direct_rsu_status[vid] = False
            log_queue.put({"type": "RSU_LEAVE", "time": env.now, "vid": vid, "pos": pos, "rid": rsu_data_for_vehicles[0]["id"]})
            v2r_inbox.append({"type": "BYE", "from": vid, "time": env.now, "pos": pos, "wait": total_wait, "to_rid": rsu_data_for_vehicles[0]["id"]})
        
        # --- NEW: Vehicle listens for RSU Broadcasts if it's within the RSU's broadcast range ---
        # Iterate through available RSU broadcast data.
        for rsu_info in rsu_data_for_vehicles:
            rid = rsu_info["id"]
            rsu_broadcast_channel = rsu_info["broadcast_channel"] # Get specific RSU's broadcast channel
            rsu_broadcast_range = rsu_info["range"] # Get specific RSU's range
            rsu_pos = rsu_info["position"]

            # Check if vehicle is within *this RSU's* broadcast range
            if in_rsu_zone(pos, rsu_pos, rsu_broadcast_range):
                # Only check for new broadcasts from this specific RSU
                last_processed_time = last_processed_rsu_broadcast_time.get(rid, -1)
                
                latest_new_broadcast = None
                for i in range(len(rsu_broadcast_channel) - 1, -1, -1):
                    broadcast_msg = rsu_broadcast_channel[i]
                    # Ensure it's from the correct RSU and it's newer than what we last processed
                    if broadcast_msg["from"] == rid and broadcast_msg["time"] > last_processed_time:
                        if latest_new_broadcast is None or broadcast_msg["time"] > latest_new_broadcast["time"]:
                            latest_new_broadcast = broadcast_msg
                    # If we hit an older broadcast from this RSU, we can stop for this RSU's channel
                    elif broadcast_msg["from"] == rid and broadcast_msg["time"] <= last_processed_time:
                        break
                
                if latest_new_broadcast:
                    log_queue.put({
                        "type": "GLOBAL_RSU_BROADCAST_RECEIVE", 
                        "time": env.now, 
                        "to_vid": vid, 
                        "from_rid": latest_new_broadcast["from"], 
                        "broadcast_time": latest_new_broadcast["time"],
                        "avg_wait": latest_new_broadcast["avg_wait"]
                    })
                    
                    # Send an acknowledgment back to the RSU
                    rsu_ack_inbox.append({
                        "type": "ACK", 
                        "from_vid": vid, 
                        "time": env.now, 
                        "to_rid": latest_new_broadcast["from"],
                        "broadcast_id": latest_new_broadcast["broadcast_id"] 
                    })
                    
                    # Update last processed time for this RSU for this vehicle
                    last_processed_rsu_broadcast_time[rid] = latest_new_broadcast["time"]
            # -------------------------------------------------------------------

        yield env.timeout(config["time_step"]) # Advance simulation by one step
    wait_log[vid] = total_wait

# ------------ RSU (proactive table-driven) ------------
def rsu(env, rid, config, vehicles_state, v2r_inbox, rsu_log, log_queue,
        rsu_broadcast_channel, rsu_ack_inbox, rsu_position, rsu_range): # Added rsu_position, rsu_range
    
    coverage  = rsu_range # Use passed in rsu_range
    interval  = config["rsus"]["broadcast_interval"]
    connected = set()
    arrival_at = {}
    table = {}
    
    while env.now < config["simulation_time"]:
        in_range = []
        # Vehicles directly connected to this RSU via V2R (within its direct coverage)
        for vid, state in vehicles_state.items():
            if state is None: 
                continue
            # Use the RSU's specific position and range for direct V2R connection
            if in_rsu_zone(state["pos"], rsu_position, coverage): 
                in_range.append(vid)
        
        new_conn = set(in_range) - connected
        gone     = connected - set(in_range)
        
        for vid in sorted(new_conn):
            log_queue.put({"type": "RSU_ARRIVED", "time": env.now, "rid": rid, "vid": vid})
            connected.add(vid)
            arrival_at[vid] = env.now
            table.setdefault(vid, {})
            table[vid]["first_seen"] = env.now
            table[vid]["last_seen"]  = env.now
        
        for vid in sorted(gone):
            log_queue.put({"type": "RSU_DEPARTED", "time": env.now, "rid": rid, "vid": vid})
            connected.remove(vid)
            rsu_log.append({"vehicle": vid, "arrival": arrival_at.get(vid, None), "departure": env.now})
            table.setdefault(vid, {})
            table[vid]["last_seen"] = env.now
        
        # Process V2R Inbox messages (range-based messages from vehicles to this RSU)
        # Filter for messages intended for *this specific RSU*
        i = 0
        while i < len(v2r_inbox):
            m = v2r_inbox[i]
            if m.get("to_rid") == rid: # Only process messages for this RSU
                vid = m["from"]
                if vid in connected: # Only process messages from currently connected vehicles for V2R inbox
                    rec = table.setdefault(vid, {})
                    rec["last_pos"]   = m["pos"]
                    rec["last_wait"]  = m.get("wait", rec.get("last_wait", 0))
                    rec["last_update"]= m["time"]
                    v2r_inbox.pop(i)
                else: # Message for this RSU, but vehicle no longer connected (can happen if vehicle just left)
                    log_queue.put({"type": "RSU_V2R_MESSAGE_OUT_OF_RANGE", "time": env.now, "rid": rid, "from_vid": vid, "msg_type": m["type"]})
                    v2r_inbox.pop(i) # Remove it anyway, RSU can't process it.
            else:
                i += 1
        
        connected_list = sorted(list(connected))
        waits = [table[v].get("last_wait", 0) for v in connected_list if v in table]
        avg_wait = mean(waits) if waits else 0

        # --- NEW: RSU places broadcast into its specific global channel ---
        broadcast_id = f"RSU{rid}_BCAST_{int(env.now)}" 
        rsu_broadcast_msg = {
            "type": "RSU_GLOBAL_BROADCAST", 
            "from": rid, 
            "time": env.now, 
            "broadcast_id": broadcast_id,
            "connected_count": len(connected_list), 
            "avg_wait": avg_wait,
            "connected_vids": connected_list
        }
        # Append to the *specific* RSU's broadcast channel
        rsu_broadcast_channel.append(rsu_broadcast_msg) 

        log_queue.put(rsu_broadcast_msg) 

        # --- NEW: RSU processes global acknowledgments (ACKs) ---
        acknowledged_by = set()
        i = 0
        while i < len(rsu_ack_inbox):
            ack_msg = rsu_ack_inbox[i]
            # Check if ACK is for this RSU and matches the current broadcast ID
            if ack_msg["to_rid"] == rid and ack_msg["broadcast_id"] == broadcast_id:
                acknowledged_by.add(ack_msg["from_vid"])
                log_queue.put({
                    "type": "RSU_ACK_RECEIVED", 
                    "time": env.now, 
                    "rid": rid, 
                    "from_vid": ack_msg["from_vid"],
                    "broadcast_id": ack_msg["broadcast_id"]
                })
                rsu_ack_inbox.pop(i) 
            else:
                i += 1
        
        if acknowledged_by:
            log_queue.put({
                "type": "RSU_BROADCAST_ACK_SUMMARY",
                "time": env.now,
                "rid": rid,
                "broadcast_id": broadcast_id,
                "ack_count": len(acknowledged_by),
                "acknowledged_vids": sorted(list(acknowledged_by))
            })

        yield env.timeout(interval) 
    
# ------------ Main (Now handles config failure) ------------
def main(log_queue):
    config = load_config()
    
    if config.get("type") == "FATAL_ERROR":
        log_queue.put(config) 
        return 

    env = simpy.Environment()
    rsu_log, wait_log, v2v_messages, v2r_inbox = [], {}, [], []
    rsu_ack_inbox = []        

    vehicles_state = {i: None for i in range(config["vehicles"]["count"])}

    # NEW: Prepare RSU data for vehicles, including their specific broadcast channels
    all_rsu_data_for_vehicles = []
    
    # Each RSU now has its *own* broadcast channel
    rsu_broadcast_channels = [[] for _ in range(config["rsus"]["count"])]

    for i in range(config["rsus"]["count"]):
        rsu_id = i
        rsu_pos = config["rsus"]["position"] # Assuming all RSUs are at the same position for simplicity
        rsu_range = config["rsus"]["range"]
        all_rsu_data_for_vehicles.append({
            "id": rsu_id,
            "position": rsu_pos,
            "range": rsu_range,
            "broadcast_channel": rsu_broadcast_channels[i] # Reference to this RSU's channel
        })
        env.process(rsu(env, rsu_id, config, vehicles_state, v2r_inbox, rsu_log, log_queue,
                        rsu_broadcast_channels[i], rsu_ack_inbox, rsu_pos, rsu_range)) # Pass specific channel and pos/range

    for i in range(config["vehicles"]["count"]):
        env.process(vehicle(env, i, config, all_rsu_data_for_vehicles, # Pass list of all RSU data
                            wait_log, v2v_messages, v2r_inbox, vehicles_state, log_queue,
                            rsu_ack_inbox))

    env.run(until=config["simulation_time"])
    log_queue.put({"type": "SIM_END", "rsu_log": rsu_log, "wait_log": wait_log})



