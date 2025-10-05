import tkinter as tk
from tkinter import ttk
import pygame

class LogWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Simulation Log")
        
        columns = ('time', 'type', 'details')
        self.tree = ttk.Treeview(root, columns=columns, show='headings')
        self.tree.heading('time', text='Time (s)'); self.tree.column('time', width=80, anchor='center')
        self.tree.heading('type', text='Event Type'); self.tree.column('type', width=180) 
        self.tree.heading('details', text='Details'); self.tree.column('details', width=500)
        
        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.root.grid_rowconfigure(0, weight=1); self.root.grid_columnconfigure(0, weight=1)

    def add_log_entry(self, message):
        """Processes and displays a single log message."""
        if not message: return
        
        time_val = message.get('time')
        time_str = f"{time_val:.1f}" if time_val is not None else ""
        event_type = message.get('type', 'Unknown')
        
        details = "" 

        if event_type == "RSU_GLOBAL_BROADCAST":
            details = (f"RSU {message.get('from')} broadcast_id:{message.get('broadcast_id')}, "
                       f"Connected: {message.get('connected_count')}, "
                       f"Avg Wait: {message.get('avg_wait'):.1f}s")
        elif event_type == "GLOBAL_RSU_BROADCAST_RECEIVE":
            details = (f"Vehicle {message.get('to_vid')} received RSU {message.get('from_rid')}'s "
                       f"broadcast (time {message.get('broadcast_time'):.1f}s), "
                       f"Avg Wait: {message.get('avg_wait'):.1f}s")
        elif event_type == "RSU_ACK_RECEIVED":
            details = (f"RSU {message.get('rid')} received ACK from Vehicle {message.get('from_vid')} "
                       f"for broadcast_id: {message.get('broadcast_id')}")
        elif event_type == "RSU_BROADCAST_ACK_SUMMARY":
            details = (f"RSU {message.get('rid')} broadcast_id:{message.get('broadcast_id')} acknowledged by "
                       f"{message.get('ack_count')} vehicles: {', '.join(map(str, message.get('acknowledged_vids', [])))}")
        # NEW: Log entry for V2R messages out of range.
        elif event_type == "RSU_V2R_MESSAGE_OUT_OF_RANGE":
            details = (f"RSU {message.get('rid')} received a {message.get('msg_type')} message from "
                       f"Vehicle {message.get('from_vid')} but vehicle was not connected (likely out of direct V2R range).")
        # --- END NEW ---
        
        else: 
            excluded_keys = ['time', 'type', 'connected_vids'] 
            details = ", ".join(f"{k}={v}" for k, v in message.items() if k not in excluded_keys)
        
        self.tree.insert('', tk.END, values=(time_str, event_type, details))
        self.tree.yview_moveto(1)

class SimVisualizer:
    def __init__(self):
        pygame.init()
        self.width, self.height = 950, 300
        
        self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
        pygame.display.set_caption("VANET Simulation")
        
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 24)
        
        self.vehicle_states = {}

        self.world_width = 1000.0 
        self.scale_factor = self.width / self.world_width

        self.rsu_position = 100; self.rsu_range = 200 # These are the config values
        self.intersections = [200, 400, 600, 800]
        
        self.BG_COLOR = (240, 240, 240); self.ROAD_COLOR = (50, 50, 50)
        self.VEHICLE_COLOR = (200, 0, 0); self.WAITING_VEHICLE_COLOR = (255, 140, 0)
        self.RSU_COLOR = (0, 150, 200); self.INTERSECTION_COLOR = (200, 200, 0)

    def handle_resize(self, event):
        self.width, self.height = event.w, event.h
        self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
        self.scale_factor = self.width / self.world_width

    def process_message(self, message):
        msg_type = message.get("type")
        vid = message.get("vid")
        
        if vid is not None: 
            if vid not in self.vehicle_states:
                self.vehicle_states[vid] = {"pos": 0, "waiting": False}
            if msg_type == "VEHICLE_MOVE":
                self.vehicle_states[vid]["pos"] = message["pos"]
                self.vehicle_states[vid]["waiting"] = False
            elif msg_type == "VEHICLE_WAIT_START":
                self.vehicle_states[vid]["pos"] = message["pos"]
                self.vehicle_states[vid]["waiting"] = True

    def draw(self):
        self.screen.fill(self.BG_COLOR)
        road_y = self.height // 2
        
        pygame.draw.line(self.screen, self.ROAD_COLOR, (0, road_y), (self.width, road_y), 5)

        rsu_start_x = int(self.rsu_position * self.scale_factor)
        rsu_width = int(self.rsu_range * self.scale_factor)
        
        rsu_surface = pygame.Surface((rsu_width, 100), pygame.SRCALPHA)
        rsu_surface.fill((*self.RSU_COLOR, 50))
        self.screen.blit(rsu_surface, (rsu_start_x, road_y - 50))
        
        for pos in self.intersections:
            intersection_x = int(pos * self.scale_factor)
            pygame.draw.line(self.screen, self.INTERSECTION_COLOR, (intersection_x, road_y - 20), (intersection_x, road_y + 20), 3)

        for vid, state in self.vehicle_states.items():
            pos_x = int(state["pos"] * self.scale_factor)
            color = self.WAITING_VEHICLE_COLOR if state["waiting"] else self.VEHICLE_COLOR
            pygame.draw.circle(self.screen, color, (pos_x, road_y), 8)
            text = self.font.render(str(vid), True, (0,0,0))
            self.screen.blit(text, (pos_x - 5, road_y - 25))

        pygame.display.flip()


