import os
import json
import urllib.request
import urllib.error
import subprocess
import sys

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "gemma4-aggressive:latest"

# ----------------- TOOL DEFINITIONS -----------------

def list_directory(path="."):
    """Lists files and folders in the specified directory."""
    try:
        abs_path = os.path.abspath(path)
        items = os.listdir(abs_path)
        result = []
        for item in items:
            full_path = os.path.join(abs_path, item)
            is_dir = os.path.isdir(full_path)
            size = os.path.getsize(full_path) if not is_dir else 0
            result.append({
                "name": item,
                "type": "directory" if is_dir else "file",
                "size_bytes": size
            })
        return json.dumps({"status": "success", "directory": abs_path, "items": result}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def read_file(path):
    """Reads and returns the contents of a text/code file."""
    try:
        abs_path = os.path.abspath(path)
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return json.dumps({"status": "success", "file": abs_path, "content": content})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def write_file(path, content):
    """Writes content to a file, creating parent directories if necessary."""
    try:
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return json.dumps({"status": "success", "file": abs_path, "message": "File written successfully."})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def run_command(command):
    """Executes a local shell command, asking for explicit user permission first."""
    print(f"\n[SAFETY TRIGGERED] The AI agent wants to execute the following command:")
    print(f"--------------------------------------------------")
    print(f"  {command}")
    print(f"--------------------------------------------------")
    
    try:
        # Prompt user for confirmation
        user_input = input("Execute this command? (y/yes to approve, any other key to reject): ").strip().lower()
        if user_input not in ('y', 'yes'):
            print("[ABORTED] Command execution rejected by user.")
            return json.dumps({"status": "aborted", "message": "Command execution was aborted by the user."})
            
        print("[EXECUTING] Running command...")
        # Run command in shell
        process = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60
        )
        
        return json.dumps({
            "status": "success",
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr
        }, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "message": "Command execution timed out after 60 seconds."})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

# Map function names to actual functions
TOOLS_MAP = {
    "list_directory": list_directory,
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command
}

# Declarations for Ollama
TOOLS_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the files and directories inside a specific folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path (e.g. '.', 'src', 'C:/Users'). Defaults to '.'."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a specific text or source code file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute or relative path to the file to read."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with specific text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The target path where the file will be saved."
                    },
                    "content": {
                        "type": "string",
                        "description": "The full text content of the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a terminal shell command (compile, test, run scripts, search, etc.). Use only when necessary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to execute."
                    }
                },
                "required": ["command"]
            }
        }
    }
]

# ----------------- AGENT CORE LOOP -----------------

def call_ollama(messages):
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "tools": TOOLS_DECLARATIONS,
        "stream": False
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            return json.loads(res_body)
    except urllib.error.URLError as e:
        print(f"\n[ERROR] Failed to communicate with Ollama server: {e}")
        print("Please ensure Ollama is running and accessible at http://localhost:11434.")
        sys.exit(1)

def run_agent_loop(user_prompt):
    print(f"\n>>> User: {user_prompt}")
    
    # Initialize message log with system instruction
    messages = [
        {
            "role": "system",
            "content": (
                "You are an agentic local developer assistant. You have access to local file tools "
                "and terminal execution. Use these tools systematically to fulfill the user's request. "
                "When you need to execute commands or inspect code, call the appropriate tool. "
                "Always check directory contents before making assumptions. Output your thought process "
                "clearly before using any tool."
            )
        },
        {"role": "user", "content": user_prompt}
    ]
    
    while True:
        print("Thinking...")
        response = call_ollama(messages)
        message = response.get("message", {})
        
        # Add the assistant response to message history
        messages.append(message)
        
        # If assistant has textual content, print it
        if message.get("content"):
            print(f"\n>>> Agent: {message['content']}")
            
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # No tool execution requested, agent is finished with this turn
            break
            
        # Execute tool calls
        for tool_call in tool_calls:
            func_name = tool_call.get("function", {}).get("name")
            arguments = tool_call.get("function", {}).get("arguments", {})
            print(f"\n[TOOL USE] Invoking tool '{func_name}' with args: {json.dumps(arguments)}")
            
            tool_func = TOOLS_MAP.get(func_name)
            if tool_func:
                # Execute the tool
                # Unpack kwargs
                try:
                    result = tool_func(**arguments)
                except Exception as ex:
                    result = json.dumps({"status": "error", "message": str(ex)})
            else:
                result = json.dumps({"status": "error", "message": f"Tool '{func_name}' is not registered."})
                
            print(f"[TOOL RESULT] Obtained response.")
            # Append the tool result back into messages history
            messages.append({
                "role": "tool",
                "content": result,
                # In Ollama API, function name or tool call identifier matches what was called
                "name": func_name
            })
            
        print("\n--- Continuing conversation with tool results ---")

# ----------------- MAIN ENTRY POINT -----------------

if __name__ == "__main__":
    print("=" * 60)
    print(f" Gemma Local Developer Agent (Powered by {MODEL_NAME})")
    print("=" * 60)
    print("Type your request (or type 'exit' or 'quit' to close the agent):")
    
    while True:
        try:
            prompt = input("\nPrompt > ").strip()
            if not prompt:
                continue
            if prompt.lower() in ('exit', 'quit'):
                print("Exiting agent. Goodbye!")
                break
            run_agent_loop(prompt)
        except KeyboardInterrupt:
            print("\nExiting agent. Goodbye!")
            break
        except Exception as e:
            print(f"\n[ERROR] An unexpected error occurred: {e}")
