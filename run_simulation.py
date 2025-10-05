import queue
import threading
import tkinter as tk
import pygame
import os

from main import main as run_sim_logic
from visualizer import LogWindow, SimVisualizer

if __name__ == "__main__":
    # --- 1. RUN SIMULATION & COLLECT DATA ---
    log_queue = queue.Queue()
    sim_thread = threading.Thread(target=run_sim_logic, args=(log_queue,), daemon=True)
    sim_thread.start()
    print("Simulation thread started. Waiting for it to complete...")
    sim_thread.join()
    print("Simulation thread finished. Collecting results...")
    
    all_events = []
    while not log_queue.empty():
        all_events.append(log_queue.get())
    all_events.sort(key=lambda x: x.get("time", 0))
    print(f"Collected {len(all_events)} simulation events. Starting visualization.")

    # --- 2. SETUP GUIS ---
    os.environ['SDL_VIDEO_WINDOW_POS'] = "50,70"
    visualizer = SimVisualizer()
    root = tk.Tk()
    root.withdraw()
    log_tk_window = tk.Toplevel(root)
    log_tk_window.geometry(f"750x{visualizer.height}") # Use initial height
    log_app = LogWindow(log_tk_window)

    # --- 3. PLAYBACK LOOP ---
    playback_speed = 1.0
    simulation_time = 0.0
    event_index = 0
    running = True

    while running:
        # --- KEY CHANGE: Handle the VIDEORESIZE event ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            # If the user resizes the window, call our handler function
            if event.type == pygame.VIDEORESIZE:
                visualizer.handle_resize(event)

        # Advance the simulation clock
        delta_time_ms = visualizer.clock.tick(60)
        simulation_time += (delta_time_ms / 1000.0) * playback_speed

        # Process events for the current time
        while event_index < len(all_events) and all_events[event_index].get("time", 0) <= simulation_time:
            current_event = all_events[event_index]
            visualizer.process_message(current_event)
            log_app.add_log_entry(current_event)
            event_index += 1

        # Redraw windows
        visualizer.draw()
        try:
            root.update()
        except tk.TclError:
            running = False

        # End after playback
        if event_index >= len(all_events) and not sim_thread.is_alive():
            print("Playback finished.")
            pygame.time.wait(3000)
            running = False

    pygame.quit()

