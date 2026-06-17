#!/usr/bin/env python3
"""
Lab Monitoring System - Web Dashboard Server
Server dashboard berbasis Flask untuk memantau komputer lab via MQTT
Hanya komputer yang menjalankan agent.py yang akan muncul di dashboard
"""

from flask import Flask, render_template_string, jsonify, request
import paho.mqtt.client as mqtt
import json
import threading
import time
from datetime import datetime
from collections import defaultdict
import uuid

# ==================== KONFIGURASI ====================
BROKER_URL = "192.168.3.223"
PORT = 1883
DASHBOARD_PORT = 5000

# ==================== FLASK APP ====================
app = Flask(__name__)

# Global data storage
computers_data = {}
last_updates = {}
mqtt_connected = False
computers_lock = threading.Lock()

# Command results storage with threading events
command_pending = {}  # {request_id: {'event': threading.Event(), 'result': None}}
command_pending_lock = threading.Lock()

# ==================== MQTT SETUP ====================
mqtt_client = None
mqtt_client_lock = threading.Lock()

def setup_mqtt():
    """Setup MQTT client"""
    global mqtt_client
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    except (ImportError, AttributeError):
        mqtt_client = mqtt.Client()
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect

def on_connect(client, userdata, flags, rc, properties=None):
    """MQTT connect callback"""
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        client.subscribe("lab/monitoring/#")
        client.subscribe("lab/command/result/#")
        print(f"[✓] Connected to MQTT broker at {BROKER_URL}:{PORT}")
        print(f"[✓] Subscribed to topic: lab/monitoring/#")
        print(f"[✓] Subscribed to topic: lab/command/result/#")
    else:
        mqtt_connected = False
        print(f"[✗] MQTT connection failed with code {rc}")

def on_disconnect(client, userdata, disconnect_flags, rc, properties=None):
    """MQTT disconnect callback"""
    global mqtt_connected
    mqtt_connected = False
    print(f"[!] Disconnected from MQTT broker")

def on_message(client, userdata, msg):
    """MQTT message callback - handles both monitoring and command results"""
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode('utf-8'))
        
        # Handle command result messages
        if topic.startswith("lab/command/result/"):
            hostname = payload.get('hostname', 'unknown')
            request_id = payload.get('request_id', 'unknown')
            command = payload.get('command', 'unknown')
            result = payload.get('result', {})
            
            with command_pending_lock:
                if request_id in command_pending:
                    command_pending[request_id]['result'] = {
                        'hostname': hostname,
                        'command': command,
                        'result': result,
                        'timestamp': payload.get('timestamp', datetime.now().isoformat()),
                    }
                    command_pending[request_id]['event'].set()
                    print(f"[✓] Command result from {hostname}: {command} -> {result.get('status', 'unknown')}")
            return
        
        # Handle monitoring messages
        computer_id = payload.get('id', 'unknown')
        status = payload.get('status', 'offline')
        
        with computers_lock:
            if status == 'online':
                computers_data[computer_id] = payload
                last_updates[computer_id] = datetime.now()
                print(f"[+] Received data from {computer_id}: CPU={payload.get('metrics', {}).get('cpu', {}).get('percent', '?')}%")
            elif status == 'offline':
                if computer_id in computers_data:
                    computers_data[computer_id]['status'] = 'offline'
                    print(f"[-] {computer_id} went offline")
                    
    except json.JSONDecodeError as e:
        print(f"[!] JSON decode error: {e}")
    except Exception as e:
        print(f"[!] Error processing message: {e}")

def generate_request_id():
    """Generate a unique request ID"""
    return str(uuid.uuid4())

def send_command(hostname, command, params=None, timeout=30):
    """
    Send a command to a specific computer via MQTT (non-blocking with event wait)
    """
    global mqtt_client
    
    with mqtt_client_lock:
        if not mqtt_connected or mqtt_client is None:
            return {'status': 'error', 'error': 'MQTT not connected'}
    
    request_id = generate_request_id()
    
    # Create event and register pending command
    event = threading.Event()
    with command_pending_lock:
        command_pending[request_id] = {'event': event, 'result': None}
    
    # Prepare command payload
    payload = {
        'command': command,
        'request_id': request_id,
        'params': params or {}
    }
    
    # Publish to command topic
    command_topic = f"lab/command/{hostname}"
    try:
        with mqtt_client_lock:
            mqtt_client.publish(command_topic, json.dumps(payload))
        print(f"[*] Command sent to {hostname}: {command} (Request ID: {request_id})")
    except Exception as e:
        with command_pending_lock:
            if request_id in command_pending:
                del command_pending[request_id]
        return {'status': 'error', 'error': f'Failed to send command: {str(e)}'}
    
    # Wait for result with timeout
    result_received = event.wait(timeout=timeout)
    
    # Get result
    with command_pending_lock:
        if request_id in command_pending:
            if result_received and command_pending[request_id]['result'] is not None:
                result = command_pending[request_id]['result']
                del command_pending[request_id]
                return result
            else:
                del command_pending[request_id]
                return {'status': 'error', 'error': f'Command timeout after {timeout} seconds'}
        else:
            return {'status': 'error', 'error': 'Command result not found'}

def mqtt_loop():
    """MQTT loop thread"""
    global mqtt_client
    while True:
        try:
            mqtt_client.connect(BROKER_URL, PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            print(f"[!] MQTT connection error: {e}")
            print(f"[!] Retrying in 5 seconds...")
            time.sleep(5)

# ==================== HTML TEMPLATE ====================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lab Monitoring System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #f5f0ff 0%, #ede5f7 50%, #f0eaff 100%);
            min-height: 100vh; color: #2d1b69;
        }
        .header {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 20px; text-align: center;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }
        .header h1 { font-size: 2.5em; margin-bottom: 5px; }
        .header p { font-size: 1.1em; opacity: 0.9; }
        .status-bar {
            background: #e8dff5; padding: 10px 20px;
            display: flex; justify-content: space-between; align-items: center;
            border-bottom: 2px solid #d4c5f0;
        }
        .status-indicator { display: flex; align-items: center; gap: 8px; }
        .status-dot {
            width: 12px; height: 12px; border-radius: 50%;
            animation: pulse 2s infinite;
        }
        .status-dot.connected { background-color: #28a745; }
        .status-dot.disconnected { background-color: #dc3545; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .summary-cards {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px; margin-bottom: 30px;
        }
        .summary-card {
            background: white; border-radius: 15px; padding: 20px;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.1);
            border-left: 5px solid #667eea; text-align: center;
        }
        .summary-card h3 { color: #6b5b95; font-size: 0.9em; text-transform: uppercase; margin-bottom: 10px; }
        .summary-card .number { font-size: 2.5em; font-weight: bold; color: #2d1b69; }
        .computers-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px;
        }
        .computer-card {
            background: white; border-radius: 15px; padding: 25px;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.15);
            border-left: 5px solid #667eea;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .computer-card:hover { transform: translateY(-3px); box-shadow: 0 6px 20px rgba(102, 126, 234, 0.25); }
        .computer-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 20px; padding-bottom: 15px; border-bottom: 2px solid #f0e6ff;
        }
        .computer-name { font-size: 1.4em; font-weight: bold; color: #2d1b69; }
        .computer-status { padding: 5px 15px; border-radius: 20px; font-size: 0.85em; font-weight: 600; }
        .status-online { background-color: #d4edda; color: #155724; }
        .status-offline { background-color: #f8d7da; color: #721c24; }
        .metrics-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
        .metric-item { background: #f8f5ff; border-radius: 10px; padding: 15px; }
        .metric-label { font-size: 0.85em; color: #6b5b95; font-weight: 600; text-transform: uppercase; margin-bottom: 8px; }
        .metric-value { font-size: 1.5em; font-weight: bold; color: #2d1b69; }
        .metric-unit { font-size: 0.5em; color: #8b7dbd; margin-left: 5px; }
        .progress-bar { height: 8px; background: #e8dff5; border-radius: 4px; margin-top: 8px; overflow: hidden; }
        .progress-fill {
            height: 100%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            border-radius: 4px; transition: width 0.3s ease;
        }
        .computer-info {
            margin-top: 15px; padding-top: 15px; border-top: 2px solid #f0e6ff;
            font-size: 0.85em; color: #6b5b95;
        }
        .computer-info span {
            display: inline-block; background: #f0eaff; padding: 3px 10px;
            border-radius: 12px; margin: 3px; font-size: 0.9em;
        }
        .control-panel { margin-top: 20px; padding-top: 15px; border-top: 2px solid #f0e6ff; }
        .control-panel h4 { color: #6b5b95; margin-bottom: 10px; font-size: 0.95em; }
        .control-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
        .control-btn {
            padding: 8px 16px; border: none; border-radius: 8px;
            font-size: 0.85em; font-weight: 600; cursor: pointer;
            transition: all 0.2s; display: flex; align-items: center; gap: 5px;
        }
        .control-btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .control-btn:active { transform: translateY(0); }
        .control-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
        .btn-kill { background: linear-gradient(135deg, #ff6b6b, #ee5a24); color: white; }
        .btn-shutdown { background: linear-gradient(135deg, #576574, #222f3e); color: white; }
        .btn-restart { background: linear-gradient(135deg, #0abde3, #10ac84); color: white; }
        .btn-processes { background: linear-gradient(135deg, #a55eea, #8854d0); color: white; }
        .btn-cancel { background: linear-gradient(135deg, #f7b731, #fa8231); color: white; }
        .btn-danger { background: linear-gradient(135deg, #eb3b5a, #d63031); color: white; }
        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: center;
        }
        .modal-overlay.active { display: flex; }
        .modal-content {
            background: white; border-radius: 15px; padding: 25px;
            max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        }
        .modal-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #f0e6ff;
        }
        .modal-header h3 { color: #2d1b69; }
        .modal-close { background: none; border: none; font-size: 1.5em; cursor: pointer; color: #8b7dbd; }
        .process-list { list-style: none; }
        .process-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 10px; border-bottom: 1px solid #f0e6ff;
        }
        .process-item:hover { background: #f8f5ff; }
        .process-name { font-weight: 600; color: #2d1b69; }
        .process-info { font-size: 0.85em; color: #8b7dbd; }
        .btn-kill-process {
            padding: 5px 12px; background: #eb3b5a; color: white;
            border: none; border-radius: 5px; cursor: pointer; font-size: 0.8em;
        }
        .btn-kill-process:hover { background: #d63031; }
        .result-message { padding: 10px 15px; border-radius: 8px; margin-top: 10px; font-size: 0.9em; }
        .result-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .result-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .result-info { background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }
        .no-computers {
            text-align: center; padding: 50px; background: white; border-radius: 15px;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.1);
        }
        .no-computers h2 { color: #6b5b95; margin-bottom: 10px; }
        .no-computers p { color: #8b7dbd; }
        .footer { text-align: center; padding: 20px; color: #8b7dbd; font-size: 0.9em; }
        @media (max-width: 768px) {
            .computers-grid { grid-template-columns: 1fr; }
            .metrics-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ Lab Monitoring System</h1>
        <p>Real-time Computer Laboratory Monitoring Dashboard</p>
    </div>
    
    <div class="status-bar">
        <div class="status-indicator">
            <div class="status-dot {{ 'connected' if mqtt_connected else 'disconnected' }}"></div>
            <span>{{ 'Connected' if mqtt_connected else 'Disconnected' }} to MQTT Broker ({{ BROKER_URL }}:{{ PORT }})</span>
        </div>
        <div>Last Update: {{ last_update_time }}</div>
    </div>
    
    <div class="container">
        {% if computers %}
        <div class="summary-cards">
            <div class="summary-card"><h3>Total Computers</h3><div class="number">{{ total_computers }}</div></div>
            <div class="summary-card"><h3>Online</h3><div class="number" style="color: #28a745;">{{ online_count }}</div></div>
            <div class="summary-card"><h3>Offline</h3><div class="number" style="color: #dc3545;">{{ offline_count }}</div></div>
            <div class="summary-card"><h3>Avg CPU Usage</h3><div class="number">{{ avg_cpu }}%</div></div>
        </div>
        
        <div class="computers-grid">
            {% for computer in computers %}
            <div class="computer-card">
                <div class="computer-header">
                    <div class="computer-name">{{ computer.name }}</div>
                    <div class="computer-status {{ 'status-online' if computer.status == 'online' else 'status-offline' }}">
                        {{ computer.status | upper }}
                    </div>
                </div>
                
                <div class="metrics-grid">
                    <div class="metric-item">
                        <div class="metric-label">CPU</div>
                        <div class="metric-value">{{ computer.cpu_name }}</div>
                        <div class="progress-bar"><div class="progress-fill" style="width: {{ computer.cpu_percent }}%;"></div></div>
                        <div style="margin-top: 5px; font-size: 0.85em; color: #8b7dbd;">{{ computer.cpu_percent }}% | {{ computer.cpu_cores }} Cores</div>
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">RAM</div>
                        <div class="metric-value">{{ computer.ram_total }}<span class="metric-unit">GB</span></div>
                        <div class="progress-bar"><div class="progress-fill" style="width: {{ computer.ram_percent }}%;"></div></div>
                        <div style="margin-top: 5px; font-size: 0.85em; color: #8b7dbd;">{{ computer.ram_used }} GB used ({{ computer.ram_percent }}%)</div>
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">Storage</div>
                        <div class="metric-value">{{ computer.storage_total }}<span class="metric-unit">GB</span></div>
                        <div class="progress-bar"><div class="progress-fill" style="width: {{ computer.storage_percent }}%;"></div></div>
                        <div style="margin-top: 5px; font-size: 0.85em; color: #8b7dbd;">{{ computer.storage_used }} GB used ({{ computer.storage_percent }}%)</div>
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">Network</div>
                        <div class="metric-value">{{ computer.network_speed }}<span class="metric-unit">Mbps</span></div>
                        <div style="margin-top: 5px; font-size: 0.85em; color: #8b7dbd;">Ping: {{ computer.latency }}ms | IP: {{ computer.ip }}</div>
                    </div>
                </div>
                
                <div class="computer-info">
                    <span>👤 {{ computer.user }}</span>
                    <span>⏱️ {{ computer.uptime }}</span>
                    <span>🖥️ {{ computer.os }}</span>
                    {% if computer.gpu %}<span>🎮 {{ computer.gpu }}</span>{% endif %}
                    <span>🕐 {{ computer.last_seen }}</span>
                </div>
                
                {% if computer.status == 'online' %}
                <div class="control-panel">
                    <h4>⚡ Remote Control</h4>
                    <div class="control-buttons">
                        <button class="control-btn btn-processes" onclick="showProcesses('{{ computer.name }}')">📋 Processes</button>
                        <button class="control-btn btn-kill" onclick="killProcessPrompt('{{ computer.name }}')">🔪 Kill App</button>
                        <button class="control-btn btn-shutdown" onclick="confirmAction('{{ computer.name }}', 'shutdown', 'Are you sure you want to SHUTDOWN this computer?')">⏻ Shutdown</button>
                        <button class="control-btn btn-restart" onclick="confirmAction('{{ computer.name }}', 'restart', 'Are you sure you want to RESTART this computer?')">🔄 Restart</button>
                        <button class="control-btn btn-cancel" onclick="confirmAction('{{ computer.name }}', 'cancel_shutdown', 'Cancel pending shutdown?')">❌ Cancel Shutdown</button>
                    </div>
                    <div id="result-{{ computer.name }}"></div>
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="no-computers">
            <h2>⏳ Waiting for computers...</h2>
            <p>No computers are currently connected. Make sure agent.py is running on client computers.</p>
        </div>
        {% endif %}
    </div>
    
    <div class="footer">
        Lab Monitoring System | MQTT Broker: {{ BROKER_URL }}:{{ PORT }} | Update Rate: Real-time
    </div>
    
    <!-- Process List Modal -->
    <div class="modal-overlay" id="processModal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="modalTitle">Running Processes</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div id="modalBody"><p>Loading processes...</p></div>
        </div>
    </div>
    
    <script>
        // Auto refresh every 5 seconds
        setTimeout(function() { location.reload(); }, 5000);
        
        let currentHostname = '';
        
        function showResult(hostname, status, message) {
            const resultDiv = document.getElementById('result-' + hostname);
            if (resultDiv) {
                const className = status === 'success' ? 'result-success' : (status === 'error' ? 'result-error' : 'result-info');
                resultDiv.innerHTML = `<div class="result-message ${className}">${message}</div>`;
                setTimeout(() => { resultDiv.innerHTML = ''; }, 8000);
            }
        }
        
        function confirmAction(hostname, action, message) {
            if (confirm(message)) {
                executeCommand(hostname, action);
            }
        }
        
        async function executeCommand(hostname, command, params = {}) {
            try {
                showResult(hostname, 'info', '⏳ Executing command...');
                
                // Disable buttons during execution
                const card = document.getElementById('result-' + hostname)?.closest('.computer-card');
                if (card) {
                    card.querySelectorAll('.control-btn').forEach(btn => btn.disabled = true);
                }
                
                const response = await fetch('/api/computers/' + hostname + '/' + command, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(params)
                });
                
                const result = await response.json();
                
                // Re-enable buttons
                if (card) {
                    card.querySelectorAll('.control-btn').forEach(btn => btn.disabled = false);
                }
                
                if (result.status === 'success') {
                    const output = result.result && result.result.output ? result.result.output.substring(0, 200) : '';
                    showResult(hostname, 'success', '✅ Command executed successfully!' + (output ? '<br><small>' + output + '</small>' : ''));
                } else {
                    const errorMsg = result.error || 'Unknown error';
                    showResult(hostname, 'error', '❌ Error: ' + errorMsg);
                }
            } catch (error) {
                showResult(hostname, 'error', '❌ Connection error: ' + error.message);
            }
        }
        
        async function showProcesses(hostname) {
            currentHostname = hostname;
            const modal = document.getElementById('processModal');
            const modalTitle = document.getElementById('modalTitle');
            const modalBody = document.getElementById('modalBody');
            
            modalTitle.textContent = 'Running Processes - ' + hostname;
            modalBody.innerHTML = '<p>Loading processes...</p>';
            modal.classList.add('active');
            
            try {
                const response = await fetch('/api/computers/' + hostname + '/processes');
                const result = await response.json();
                
                if (result.status === 'success' && result.result && result.result.output) {
                    const output = result.result.output;
                    const lines = output.split('\\n').filter(line => line.trim());
                    
                    if (lines.length > 0) {
                        let html = '<ul class="process-list">';
                        for (let i = 1; i < Math.min(lines.length, 50); i++) {
                            const line = lines[i].trim();
                            if (line) {
                                const parts = line.split(/\\s+/);
                                const processName = parts[0] || 'Unknown';
                                const pid = parts[1] || 'N/A';
                                const cpu = parts[2] || '';
                                const mem = parts[3] || '';
                                
                                html += `<li class="process-item">
                                    <div>
                                        <div class="process-name">${processName}</div>
                                        <div class="process-info">PID: ${pid}${cpu ? ' | CPU: ' + cpu : ''}${mem ? ' | MEM: ' + mem : ''}</div>
                                    </div>
                                    <button class="btn-kill-process" onclick="killProcessByName('${hostname}', '${processName}')">Kill</button>
                                </li>`;
                            }
                        }
                        html += '</ul>';
                        modalBody.innerHTML = html;
                    } else {
                        modalBody.innerHTML = '<p>No processes found or unable to retrieve process list.</p>';
                    }
                } else {
                    modalBody.innerHTML = '<p>Error loading processes: ' + (result.error || 'Unknown error') + '</p>';
                }
            } catch (error) {
                modalBody.innerHTML = '<p>Error: ' + error.message + '</p>';
            }
        }
        
        function closeModal() {
            const modal = document.getElementById('processModal');
            modal.classList.remove('active');
        }
        
        async function killProcessByName(hostname, processName) {
            if (confirm('Are you sure you want to kill "' + processName + '" on ' + hostname + '?')) {
                closeModal();
                try {
                    showResult(hostname, 'info', '⏳ Killing process...');
                    
                    const response = await fetch('/api/computers/' + hostname + '/kill', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ process_name: processName })
                    });
                    
                    const result = await response.json();
                    
                    if (result.status === 'success') {
                        showResult(hostname, 'success', '✅ Process "' + processName + '" killed successfully!');
                    } else {
                        showResult(hostname, 'error', '❌ Error: ' + (result.error || 'Unknown error'));
                    }
                } catch (error) {
                    showResult(hostname, 'error', '❌ Connection error: ' + error.message);
                }
            }
        }
        
        function killProcessPrompt(hostname) {
            const processName = prompt('Enter the exact process name to kill (e.g., chrome.exe, notepad.exe):');
            if (processName) {
                killProcessByName(hostname, processName);
            }
        }
        
        document.getElementById('processModal').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });
    </script>
</body>
</html>
'''

# ==================== ROUTES ====================
@app.route('/')
def dashboard():
    """Main dashboard page"""
    with computers_lock:
        computers_list = []
        online_count = 0
        offline_count = 0
        total_cpu = 0
        
        for computer_id, data in sorted(computers_data.items()):
            status = data.get('status', 'offline')
            last_update = last_updates.get(computer_id)
            is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
            
            if status == 'online' and is_recent:
                online_count += 1
            else:
                offline_count += 1
            
            metrics = data.get('metrics', {})
            network = data.get('network', {})
            info = data.get('info', {})
            
            cpu_data = metrics.get('cpu', {})
            ram_data = metrics.get('ram', {})
            storage_data = metrics.get('storage', {})
            
            gpu_name = ""
            gpu_list = metrics.get('gpu', [])
            if gpu_list:
                gpu_name = gpu_list[0].get('name', '')[:30]
            
            computer_info = {
                'name': computer_id,
                'status': 'online' if (status == 'online' and is_recent) else 'offline',
                'cpu_name': cpu_data.get('cpu_name', info.get('cpu_name', 'Unknown'))[:40],
                'cpu_percent': cpu_data.get('percent', 0),
                'cpu_cores': cpu_data.get('cores', 0),
                'ram_total': ram_data.get('total_gb', 0),
                'ram_used': ram_data.get('used_gb', 0),
                'ram_percent': metrics.get('ram_percent', 0),
                'storage_total': storage_data.get('total_gb', 0),
                'storage_used': storage_data.get('used_gb', 0),
                'storage_percent': storage_data.get('percent', 0),
                'network_speed': network.get('down_mbps', 0),
                'latency': network.get('latency_ms', 0),
                'ip': network.get('ip', 'N/A'),
                'user': data.get('user', 'N/A'),
                'uptime': info.get('uptime', 'N/A'),
                'os': info.get('os', 'N/A'),
                'gpu': gpu_name,
                'last_seen': last_update.strftime('%H:%M:%S') if last_update else 'Never'
            }
            
            computers_list.append(computer_info)
            total_cpu += cpu_data.get('percent', 0)
        
        avg_cpu = round(total_cpu / len(computers_list)) if computers_list else 0
        
        return render_template_string(
            HTML_TEMPLATE,
            computers=computers_list,
            total_computers=len(computers_list),
            online_count=online_count,
            offline_count=offline_count,
            avg_cpu=avg_cpu,
            mqtt_connected=mqtt_connected,
            BROKER_URL=BROKER_URL,
            PORT=PORT,
            last_update_time=datetime.now().strftime('%H:%M:%S')
        )

@app.route('/api/computers')
def api_computers():
    """API endpoint for computer data"""
    with computers_lock:
        computers_list = []
        for computer_id, data in sorted(computers_data.items()):
            status = data.get('status', 'offline')
            last_update = last_updates.get(computer_id)
            is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
            
            if status == 'online' and is_recent:
                computers_list.append({
                    'id': computer_id,
                    'status': 'online',
                    'data': data,
                    'last_update': last_update.isoformat()
                })
        
        return jsonify({
            'computers': computers_list,
            'count': len(computers_list),
            'mqtt_connected': mqtt_connected
        })

@app.route('/api/command', methods=['POST'])
def api_command():
    """API endpoint for sending remote commands to computers"""
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'error': 'No JSON data provided'}), 400
    
    hostname = data.get('hostname')
    command = data.get('command')
    params = data.get('params', {})
    
    if not hostname:
        return jsonify({'status': 'error', 'error': 'Hostname is required'}), 400
    if not command:
        return jsonify({'status': 'error', 'error': 'Command is required'}), 400
    
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    result = send_command(hostname, command, params)
    return jsonify(result)

@app.route('/api/computers/<hostname>/processes')
def api_get_processes(hostname):
    """Get list of running processes from a specific computer"""
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    result = send_command(hostname, 'tasklist')
    return jsonify(result)

@app.route('/api/computers/<hostname>/kill', methods=['POST'])
def api_kill_process(hostname):
    """Kill a process on a specific computer by name or PID"""
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'error': 'No JSON data provided'}), 400
    
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    process_name = data.get('process_name')
    pid = data.get('pid')
    
    if process_name:
        result = send_command(hostname, 'taskkill', {'process_name': process_name})
    elif pid:
        result = send_command(hostname, 'kill_pid', {'pid': pid})
    else:
        return jsonify({'status': 'error', 'error': 'Either process_name or pid is required'}), 400
    
    return jsonify(result)

@app.route('/api/computers/<hostname>/shutdown', methods=['POST'])
def api_shutdown(hostname):
    """Shutdown a specific computer"""
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    result = send_command(hostname, 'shutdown')
    return jsonify(result)

@app.route('/api/computers/<hostname>/restart', methods=['POST'])
def api_restart(hostname):
    """Restart a specific computer"""
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    result = send_command(hostname, 'restart')
    return jsonify(result)

@app.route('/api/computers/<hostname>/cancel_shutdown', methods=['POST'])
def api_cancel_shutdown(hostname):
    """Cancel pending shutdown on a specific computer"""
    with computers_lock:
        if hostname not in computers_data:
            return jsonify({'status': 'error', 'error': f'Computer {hostname} not found'}), 404
        computer_data = computers_data.get(hostname, {})
        is_online = computer_data.get('status') == 'online'
        last_update = last_updates.get(hostname)
        is_recent = last_update and (datetime.now() - last_update).total_seconds() < 30
        
        if not (is_online and is_recent):
            return jsonify({'status': 'error', 'error': f'Computer {hostname} is offline'}), 400
    
    result = send_command(hostname, 'cancel_shutdown')
    return jsonify(result)

# ==================== MAIN ====================
def main():
    """Main function"""
    setup_mqtt()
    mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
    mqtt_thread.start()
    
    time.sleep(2)
    
    print(f"\n{'='*60}")
    print(f"  Lab Monitoring System - Web Dashboard")
    print(f"{'='*60}")
    print(f"  Dashboard URL: http://localhost:{DASHBOARD_PORT}")
    print(f"  MQTT Broker: {BROKER_URL}:{PORT}")
    print(f"{'='*60}")
    print(f"\n  Starting server...")
    
    app.run(host='0.0.0.0', port=DASHBOARD_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()