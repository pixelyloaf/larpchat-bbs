#!/usr/bin/env python3
import sys
import os
import json
import socket
import threading
import requests
import time

try:
    import termios
except ImportError:
    termios = None
# --- Configuration ---
HTTP_URL = "http://localhost:6767"
TCP_HOST = "localhost"
TCP_PORT = 3033
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "client_credentials.json")

# --- Global State ---
state = {
    "token": None,
    "username": None,
    "bbs_user": "default_profile",
    "active_room": "general",
    "rooms": [],
    "running": True
}

# --- ANSI Color Helpers ---
CLR_RESET = "\x1b[0m"
CLR_CYAN = "\x1b[1;36m"
CLR_GREEN = "\x1b[1;32m"
CLR_YELLOW = "\x1b[1;33m"
CLR_RED = "\x1b[1;31m"
CLR_MAGENTA = "\x1b[1;35m"

def print_prompt():
    """Prints the dynamic input prompt safely."""
    sys.stdout.write(f"\r\x1b[K{CLR_MAGENTA}[{state['active_room']}]{CLR_RESET} > ")
    sys.stdout.flush()

def load_credentials():
    """Loads saved login token for the active BBS user profile."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            try:
                data = json.load(f)
                profile = data.get(state["bbs_user"])
                if profile:
                    state["token"] = profile.get("token")
                    state["username"] = profile.get("username")
            except Exception:
                pass

def save_credentials(username, token):
    """Saves credentials mapped to the active BBS user profile."""
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
            
    data[state["bbs_user"]] = {"username": username, "token": token}
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def detect_bbs_user():
    """Detects if running inside BBS and extracts the caller's identity."""
    if len(sys.argv) > 1:
        node_dir = sys.argv[1]
        door_sys_path = os.path.join(node_dir, "DOOR.SYS")
        if os.path.exists(door_sys_path):
            try:
                with open(door_sys_path, "r") as f:
                    lines = f.readlines()
                    if len(lines) >= 10:
                        state["bbs_user"] = lines[9].strip().lower()
            except Exception:
                pass

def authenticate():
    """Handles prompt logins and account routing with a robust input loop."""
    if state["token"] and state["username"]:
        print(f"{CLR_GREEN}Auto-logged in as chat user: {state['username']}{CLR_RESET}\n")
        return True

    print(f"{CLR_CYAN}--- Aurora Chat Network Connection ---{CLR_RESET}")
    

    while True:
        time.sleep(0.1)
        if termios and sys.stdin.isatty():
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except Exception:
                pass
        username = input("Username: ").strip()
        if username:  # Only proceed if they actually typed something
            break


    while True:
        # Give the terminal a split second to register the username's Enter key
        time.sleep(0.1) 
        if termios and sys.stdin.isatty():
            try:
                # Flush out any leftover trailing \r or \n from the username input
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except Exception:
                pass
        
        password = input("Password: ").strip()
        if password:  # Only proceed if it's not blank. If it is blank, it just loops.
            break

    # Server expects a text pipeline string format: username|password|
    payload = f"{username}|{password}|"
    try:
        response = requests.post(f"{HTTP_URL}/api/login", data=payload, headers={"Content-Type": "text/plain"})
        if "ERR_WRONG_PASS" in response.text or response.status_code != 200:
            print(f"{CLR_RED}Invalid username or password.{CLR_RESET}")
            return False
        
        # Clean server token format split: token|\n
        token = response.text.split("|")[0].strip()
        state["token"] = token
        state["username"] = username
        save_credentials(username, token)
        print(f"{CLR_GREEN}Login Successful! Welcome {username}.{CLR_RESET}\n")
        return True
    except Exception as e:
        print(f"{CLR_RED}Could not reach login server: {e}{CLR_RESET}")
        return False
def fetch_rooms():
    """Retrieves available room lists from the API server."""
    try:
        response = requests.post(f"{HTTP_URL}/api/rooms")
        parts = response.text.split("|")
        if len(parts) > 2:
            state["rooms"] = [r for r in parts[1:-1] if r]
            if state["active_room"] not in state["rooms"] and state["rooms"]:
                state["active_room"] = state["rooms"][0]
    except Exception:
        state["rooms"] = ["general", "announcements", "bots", "lounge", "luigi chat"]

def send_chat(message):
    """Dispatches a chat transmission out to the HTTP gateway."""
    if not message.strip():
        return
    payload = f"{message}|{state['active_room']}|"
    headers = {"auth": state["token"], "Content-Type": "text/plain"}
    try:
        res = requests.post(f"{HTTP_URL}/api/chat", data=payload, headers=headers)
        if "ERR_INVALID_TOKEN" in res.text or "ERR_WHAT_THE_HECK" in res.text:
            print(f"\n{CLR_RED}Session expired. Please use /switch to log back in.{CLR_RESET}")
        elif "ERR_FAKE_ROOM_YOU_MORON" in res.text:
            print(f"\n{CLR_RED}Error: Server rejected destination channel.{CLR_RESET}")
        elif "ERR_NO_RIGHTS" in res.text:
            print(f"\n{CLR_RED}Error: You do not have permissions to post here.{CLR_RESET}")
    except Exception as e:
        print(f"\n{CLR_RED}Error broadcasting packet: {e}{CLR_RESET}")

def tcp_listener():
    """Background listener managing raw incoming downstream socket broadcasts."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((TCP_HOST, TCP_PORT))
        s.settimeout(1.0)
    except Exception as e:
        sys.stdout.write(f"\r\x1b[K{CLR_RED}Warning: TCP Live-Stream Offline ({e}){CLR_RESET}\n")
        print_prompt()
        return

    buffer = ""
    while state["running"]:
        try:
            data = s.recv(1024).decode('utf-8')
            if not data:
                break
            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.strip():
                    # Expected syntax payload: username|message|room||
                    parts = line.split("|")
                    if len(parts) >= 3:
                        sender = parts[0]
                        msg_content = parts[1]
                        msg_room = parts[2]
                        
                        # Only render message if it belongs to user's active channel focus
                        if msg_room == state["active_room"]:
                            sys.stdout.write(f"\r\x1b[K{CLR_CYAN}<{sender}>{CLR_RESET} {msg_content}\n")
                            print_prompt()
        except socket.timeout:
            continue
        except Exception:
            break
    s.close()

def display_help():
    print(f"\n{CLR_YELLOW}--- Command List ---{CLR_RESET}")
    print(f"  /rooms          - List all channels available on server")
    print(f"  /join <name>    - Change focus to another conversation channel")
    print(f"  /switch         - Wipe stored configuration data and swap users")
    print(f"  /help           - Bring up this interface summary window")
    print(f"  /quit           - Drop connection matrix and go back to BBS")
    print("---------------------\n")

def main_loop():
    print(f"{CLR_GREEN}Connected to Server Matrix! Type {CLR_YELLOW}/help{CLR_GREEN} for system commands.{CLR_RESET}")
    print_prompt()
    
    while state["running"]:
        try:
            user_input = input().strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            print_prompt()
            continue

        if user_input.startswith("/"):
            parts = user_input.split(" ", 1)
            cmd = parts[0].lower()
            
            if cmd == "/quit":
                state["running"] = False
                break 
            elif cmd == "/logout":
                # Don't tell anyone about this command!
                state["token"] = None
                state["username"] = None
                save_credentials("", "")
                print(f"\n{CLR_YELLOW}Logged out and credentials cleared.{CLR_RESET}")
                state["running"] = False
                break
            elif cmd == "/help":
                display_help()
                print_prompt()
            elif cmd == "/rooms":
                fetch_rooms()
                print(f"\n{CLR_YELLOW}Channels available:{CLR_RESET} {', '.join(state['rooms'])}")
                print_prompt()
            elif cmd == "/join":
                if len(parts) < 2:
                    print(f"{CLR_RED}Usage: /join <room_name>{CLR_RESET}")
                else:
                    target_room = parts[1].strip()
                    if target_room in state["rooms"]:
                        state["active_room"] = target_room
                        print(f"\n{CLR_GREEN}Switched window focus to room: #{target_room}{CLR_RESET}\n")
                    else:
                        print(f"{CLR_RED}Room '{target_room}' does not exist.{CLR_RESET}")
                print_prompt()
            elif cmd == "/switch":
                state["token"] = None
                state["username"] = None
                save_credentials("", "")
                print(f"\x1b[2J\x1b[H{CLR_YELLOW}Credentials scrubbed.{CLR_RESET}")
                if not authenticate():
                    state["running"] = False
                    break
                else:
                    fetch_rooms()
                print_prompt()
            else:
                print(f"{CLR_RED}Unknown routing directive. Type /help{CLR_RESET}")
                print_prompt()
        else:
            send_chat(user_input)
            print_prompt()

def main():
    detect_bbs_user()
    load_credentials()
    
    if not authenticate():
        sys.exit(1)
        
    fetch_rooms()


    listener_thread = threading.Thread(target=tcp_listener, daemon=True)
    listener_thread.start()


    main_loop()

    state["running"] = False
    print(f"\n{CLR_CYAN}Disconnecting from Aurora Matrix and returning to BBS...{CLR_RESET}")
    

    time.sleep(1.0)
    

    if termios and sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin, termios.TCIOFLUSH)
        except Exception:
            pass
   

if __name__ == "__main__":
    main()
