import psutil
import time
import socket
import json
import platform
import getpass
import subprocess
import os
import paho.mqtt.client as mqtt
from datetime import datetime
from threading import Lock
import shlex

# GPU Monitoring imports
try:
    import pynvml
    PYNVML_AVAILABLE = True
except:
    PYNVML_AVAILABLE = False

try:
    import win32com.client
    WIN32_AVAILABLE = True
except:
    WIN32_AVAILABLE = False

# --- KONFIGURASI ---
HOSTNAME = socket.gethostname()
TOPIC = f"lab/monitoring/{HOSTNAME}"
BROKER_URL = "192.168.3.223"
PORT = 1883
PING_TARGET = "8.8.8.8"

# Ambil Spek CPU Sekali Saja (Statik)
CPU_THREADS = psutil.cpu_count(logical=True)
CPU_CORES = psutil.cpu_count(logical=False)
CPU_NAME = platform.processor() if platform.processor() else "Unknown CPU"

# Try to get more detailed CPU name on Windows
if platform.system() == "Windows":
    try:
        result = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                CPU_NAME = lines[1].strip()
    except:
        pass

# Initialize GPU monitoring
def init_gpu_monitoring():
    """Initialize GPU monitoring for NVIDIA GPUs"""
    if PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            return True
        except:
            return False
    return False

GPU_INITIALIZED = init_gpu_monitoring()

def get_gpu_info():
    """Get GPU information for all GPUs (NVIDIA, AMD, Intel)"""
    gpus = []
    
    # Get NVIDIA GPU info via pynvml
    if GPU_INITIALIZED:
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                
                # Get memory info
                memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                memory_used_gb = round(memory_info.used / (1024**3), 2)
                memory_total_gb = round(memory_info.total / (1024**3), 2)
                
                # Get temperature
                try:
                    temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except:
                    temperature = 0
                
                # Get utilization
                try:
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_util = utilization.gpu
                    memory_util = utilization.memory
                except:
                    gpu_util = 0
                    memory_util = 0
                
                gpus.append({
                    "name": name,
                    "type": "NVIDIA",
                    "temperature": temperature,
                    "utilization": gpu_util,
                    "memory_util": memory_util,
                    "memory_used_gb": memory_used_gb,
                    "memory_total_gb": memory_total_gb
                })
        except Exception as e:
            print(f"Error getting NVIDIA GPU info: {e}")
    
    # Get AMD/Intel GPU info via WMI (Windows only)
    if WIN32_AVAILABLE and platform.system() == "Windows":
        try:
            wmi = win32com.client.GetObject("winmgmts:")
            gpu_list = wmi.InstancesOf("Win32_VideoController")
            
            for gpu in gpu_list:
                gpu_name = gpu.Name
                # Skip if already captured by pynvml (check if NVIDIA)
                if "NVIDIA" in gpu_name and gpus:
                    continue
                
                # Get adapter RAM
                adapter_ram = gpu.AdapterRAM
                if adapter_ram:
                    memory_total_gb = round(adapter_ram / (1024**3), 1)
                else:
                    memory_total_gb = 0
                
                # Get driver version
                driver_version = gpu.DriverVersion if gpu.DriverVersion else "N/A"
                
                gpus.append({
                    "name": gpu_name,
                    "type": "AMD/Intel" if "AMD" in gpu_name or "Intel" in gpu_name else "Unknown",
                    "temperature": 0,  # Not available via WMI
                    "utilization": 0,  # Not available via WMI
                    "memory_util": 0,
                    "memory_used_gb": 0,
                    "memory_total_gb": memory_total_gb,
                    "driver_version": driver_version
                })
        except Exception as e:
            print(f"Error getting AMD/Intel GPU info: {e}")
    
    # Fallback: Try using subprocess to get GPU info
    if not gpus:
        try:
            result = subprocess.run(['wmic', 'path', 'win32_VideoController', 'get', 'name'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[1:]  # Skip header
                for line in lines:
                    name = line.strip()
                    if name:
                        gpus.append({
                            "name": name,
                            "type": "Unknown",
                            "temperature": 0,
                            "utilization": 0,
                            "memory_util": 0,
                            "memory_used_gb": 0,
                            "memory_total_gb": 0
                        })
        except:
            pass
    
    return gpus

def get_active_interface_via_ping():
    print("[*] Mencari interface aktif dengan akses internet...")
    is_windows = platform.system() == "Windows"
    addrs = psutil.net_if_addrs()
    
    for intf, addr_list in addrs.items():
        if intf.lower() in ['lo', 'loopback', 'localhost']: continue
            
        for addr in addr_list:
            if addr.family == socket.AF_INET:
                ip_address = addr.address
                try:
                    if is_windows:
                        cmd = ["ping", "-n", "1", "-w", "500", "-S", ip_address, PING_TARGET]
                    else:
                        cmd = ["ping", "-c", "1", "-W", "1", "-I", intf, PING_TARGET]
                    
                    startupinfo = None
                    if is_windows:
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    
                    subprocess.check_output(cmd, startupinfo=startupinfo, stderr=subprocess.STDOUT)
                    print(f"    [+] Terpilih: {intf} ({ip_address})")
                    return intf
                except: continue
    return list(addrs.keys())[0] if addrs else "eth0"

INTERFACE_NAME = get_active_interface_via_ping()

def get_interface_ip_mac(interface_name):
    """Get the IP address and MAC address for the given interface."""
    addrs = psutil.net_if_addrs()
    ip_address = None
    mac_address = None
    if interface_name in addrs:
        for addr in addrs[interface_name]:
            if addr.family == socket.AF_INET and not ip_address:
                ip_address = addr.address
            if addr.family == psutil.AF_LINK and not mac_address:
                mac_address = addr.address
    return ip_address or "N/A", mac_address or "N/A"

def get_top_processes(limit=5):
    """Get top processes by CPU usage."""
    processes = []
    try:
        for proc in psutil.process_iter(['name', 'cpu_percent', 'memory_percent']):
            try:
                pinfo = proc.info
                if pinfo['name'] and pinfo['cpu_percent']:
                    processes.append((pinfo['name'], round(pinfo['cpu_percent'], 1), round(pinfo['memory_percent'] or 0, 1)))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        processes.sort(key=lambda x: x[1], reverse=True)
    except:
        pass
    return [{"name": p[0], "cpu": p[1], "mem": p[2]} for p in processes[:limit]]

def get_largest_files(limit=5):
    """Get largest files from common locations."""
    files = []
    search_paths = []
    is_windows = platform.system() == "Windows"
    if is_windows:
        search_paths = [os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\Public'), 'Desktop'),
                       os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\Public'), 'Documents'),
                       os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\Public'), 'Downloads')]
    else:
        search_paths = [os.path.expanduser('~/Desktop'),
                       os.path.expanduser('~/Documents'),
                       os.path.expanduser('~/Downloads')]
    try:
        for path in search_paths:
            if os.path.exists(path):
                for root, dirs, filenames in os.walk(path, topdown=True):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('$')]
                    if len(files) >= limit * 3:
                        break
                    for f in filenames[:100]:
                        try:
                            fpath = os.path.join(root, f)
                            if os.path.isfile(fpath) and not os.path.islink(fpath):
                                size = os.path.getsize(fpath)
                                if size > 1024 * 1024:  # > 1MB
                                    files.append((fpath, size))
                        except:
                            continue
                    if len(files) >= limit * 3:
                        break
        files.sort(key=lambda x: x[1], reverse=True)
    except:
        pass
    return [{"name": os.path.basename(f[0]), "path": f[0], "size_mb": round(f[1] / (1024 * 1024), 1)} for f in files[:limit]]

# --- FUNGSI METRIK ---
def get_uptime():
    try:
        uptime_seconds = time.time() - psutil.boot_time()
        return f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m"
    except: return "N/A"

def get_latency(host):
    try:
        is_windows = platform.system() == "Windows"
        cmd = ["ping", "-n" if is_windows else "-c", "1", "-w" if is_windows else "-W", "1", host]
        startupinfo = None
        if is_windows:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        output = subprocess.check_output(cmd, startupinfo=startupinfo, stderr=subprocess.STDOUT, universal_newlines=True)
        if "time=" in output:
            val = output.split("time=")[1].split("ms")[0].strip()
            return int(float(val))
    except: return 999
    return 0

def get_net_usage(interface):
    try:
        net_io = psutil.net_io_counters(pernic=True)
        if interface in net_io:
            return net_io[interface].bytes_recv, net_io[interface].bytes_sent
    except: pass
    return 0, 0

# ==================== COMMAND EXECUTION HANDLER ====================
# Whitelist command - hanya command ini yang diizinkan
ALLOWED_COMMANDS = {
    'tasklist': 'tasklist' if platform.system() == 'Windows' else 'ps aux',
    'ipconfig': 'ipconfig' if platform.system() == 'Windows' else 'ifconfig',
    'whoami': 'whoami',
    'systeminfo': 'systeminfo' if platform.system() == 'Windows' else 'uname -a',
    'taskkill': 'taskkill /IM {process_name} /F' if platform.system() == 'Windows' else 'killall {process_name}',
    'kill_pid': 'taskkill /PID {pid} /F' if platform.system() == 'Windows' else 'kill -9 {pid}',
    'shutdown': 'shutdown /s /t 30 /c "PC akan dimatikan oleh admin lab" /f' if platform.system() == 'Windows' else 'shutdown -h +1 "PC akan dimatikan oleh admin lab"',
    'restart': 'shutdown /r /t 30 /c "PC akan direstart oleh admin lab" /f' if platform.system() == 'Windows' else 'shutdown -r +1 "PC akan direstart oleh admin lab"',
    'cancel_shutdown': 'shutdown /a' if platform.system() == 'Windows' else 'shutdown -c',
}

command_lock = Lock()

def execute_command(command_name, timeout=10, params=None):
    """
    Execute whitelisted command dengan timeout
    
    Args:
        command_name: nama command dari whitelist
        timeout: timeout dalam detik (default 10)
        params: dict parameter tambahan untuk command (misal process_name untuk taskkill)
    
    Returns:
        dict dengan status dan output/error
    """
    if command_name not in ALLOWED_COMMANDS:
        return {
            "status": "error",
            "error": f"Command '{command_name}' tidak ada di whitelist",
            "output": ""
        }
    
    try:
        with command_lock:  # Prevent concurrent execution
            cmd_template = ALLOWED_COMMANDS[command_name]
            params = params or {}
            
            # Validate: jika template mengandung placeholder, parameter wajib ada
            if '{process_name}' in cmd_template and 'process_name' not in params:
                return {
                    "status": "error",
                    "error": "Parameter 'process_name' diperlukan untuk command taskkill",
                    "output": ""
                }
            if '{pid}' in cmd_template and 'pid' not in params:
                return {
                    "status": "error",
                    "error": "Parameter 'pid' diperlukan untuk command kill_pid",
                    "output": ""
                }
            
            # Substitute parameters into command template
            cmd = cmd_template
            for key, value in params.items():
                placeholder = '{' + key + '}'
                if placeholder in cmd:
                    cmd = cmd.replace(placeholder, str(value))
            
            is_windows = platform.system() == 'Windows'
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=is_windows
            )
            
            output = result.stdout if result.stdout else result.stderr
            
            return {
                "status": "success",
                "output": output,
                "error": result.stderr if result.returncode != 0 else ""
            }
    
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "error": f"Command timeout setelah {timeout} detik",
            "output": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "output": ""
        }

def on_command_message(client, userdata, msg):
    """
    Handler untuk command dari backend via MQTT
    Topic: lab/command/{HOSTNAME}
    """
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        command_name = payload.get('command')
        request_id = payload.get('request_id', 'unknown')
        
        print(f"[*] Command diterima: {command_name} (ID: {request_id})")
        
        # Extract additional parameters (e.g. process_name for taskkill)
        params = payload.get('params', {})
        
        # Execute command dengan timeout 10 detik
        result = execute_command(command_name, timeout=10, params=params)
        
        # Siapkan response
        response = {
            "hostname": HOSTNAME,
            "request_id": request_id,
            "command": command_name,
            "timestamp": datetime.now().isoformat(),
            "result": result
        }
        
        # Publish hasil ke topic response
        response_topic = f"lab/command/result/{HOSTNAME}"
        client.publish(response_topic, json.dumps(response))
        print(f"[✓] Hasil command dipublikasi ke {response_topic}")
        
    except json.JSONDecodeError as e:
        print(f"[!] Error parsing command payload: {e}")
    except Exception as e:
        print(f"[!] Error handling command: {e}")

# --- SETUP MQTT ---
# Support paho-mqtt v1.x dan v2.x
def on_connect(client, userdata, connect_flags, rc, properties=None):
    if rc == 0:
        print(f"[✓] MQTT Terhubung ke {BROKER_URL}:{PORT}")
        # Subscribe ke command topic
        command_topic = f"lab/command/{HOSTNAME}"
        client.subscribe(command_topic)
        print(f"[✓] Subscribe ke {command_topic}")
    else:
        print(f"[✗] MQTT Gagal dengan kode {rc}")

def on_disconnect(client, userdata, disconnect_flags, rc, properties=None):
    if rc != 0:
        print(f"[!] Putus koneksi tidak terduga. Kode: {rc}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    pass

try:
    from paho.mqtt.enums import CallbackAPIVersion
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
except (ImportError, AttributeError):
    client = mqtt.Client()

client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_publish = on_publish
# Setup message callback untuk command topic
client.message_callback_add(f"lab/command/{HOSTNAME}", on_command_message)
client.will_set(TOPIC, json.dumps({"id": HOSTNAME, "status": "offline"}), retain=True)

# ==================== GRACEFUL SHUTDOWN ====================
import signal, sys

running = True  # Flag untuk stop while loop

def shutdown(signum=None, frame=None):
    global running
    print(f"\n[!] Shutting down agent '{HOSTNAME}'...")
    running = False  # Stop while loop, cleanup dilakukan setelah loop

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

mqtt_connected = False
try:
    print(f"[*] Menghubungkan ke broker MQTT {BROKER_URL}:{PORT}...")
    client.connect(BROKER_URL, PORT, 60)
    client.loop_start()
    mqtt_connected = True
except socket.gaierror as e:
    print(f"[✗] Kesalahan DNS/Jaringan: Tidak bisa resolve {BROKER_URL} - {e}")
except ConnectionRefusedError as e:
    print(f"[✗] Koneksi Ditolak: Broker {BROKER_URL}:{PORT} tidak menerima koneksi - {e}")
except TimeoutError as e:
    print(f"[✗] Koneksi Timeout: Broker {BROKER_URL}:{PORT} tidak merespons - {e}")
except Exception as e:
    print(f"[✗] Kesalahan MQTT: {type(e).__name__}: {e}")

old_rx, old_tx = get_net_usage(INTERFACE_NAME)
last_time = time.time()

# Pre-calc ip/mac once (could also refresh each loop if network changes)
IP_ADDRESS, MAC_ADDRESS = get_interface_ip_mac(INTERFACE_NAME)

# Caching timers & values for high-frequency millisecond updates (0.1s / 100ms)
# Separates fast-changing metrics (CPU, RAM, network speed) from slow-changing ones
last_latency_time = 0
last_storage_time = 0
last_processes_time = 0
last_files_time = 0
last_gpu_time = 0
last_freq_time = 0

cached_latency = 0
cached_storage_total = 0
cached_storage_used = 0
cached_storage_free = 0
cached_storage_percent = 0
cached_processes = []
cached_files = []
cached_gpu_info = []
cached_current_ghz = 0
cached_max_ghz = 0

# Set target refresh interval (e.g. 0.1s for millisecond real-time responsiveness)
REFRESH_INTERVAL = 0.1

while running:
    try:
        current_time = time.time()
        elapsed = current_time - last_time
        last_time = current_time

        # Fast metric: Network RX/TX usage
        current_rx_bytes, current_tx_bytes = get_net_usage(INTERFACE_NAME)
        down_mbps = ((current_rx_bytes - old_rx) * 8 / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        old_rx, old_tx = current_rx_bytes, current_tx_bytes

        # Fast metric: RAM Virtual Memory
        mem = psutil.virtual_memory()

        # Slow metric: Latency (Ping) - Refresh every 5 seconds to avoid overhead
        if current_time - last_latency_time >= 5.0:
            cached_latency = get_latency(PING_TARGET)
            last_latency_time = current_time

        # Slow metric: Storage - Refresh every 10 seconds to avoid disk wear
        if current_time - last_storage_time >= 10.0:
            storage_total = 0
            storage_used = 0
            storage_free = 0

            if platform.system() == "Windows":
                for part in psutil.disk_partitions(all=False):
                    fstype = (part.fstype or "").lower()
                    if fstype in {"tmpfs", "devtmpfs"}:
                        continue
                    if not part.mountpoint:
                        continue
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        storage_total += usage.total
                        storage_used += usage.used
                        storage_free += usage.free
                    except Exception:
                        continue
            else:
                for part in psutil.disk_partitions(all=False):
                    fstype = (part.fstype or "").lower()
                    if fstype in {"tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay"}:
                        continue
                    if not part.mountpoint:
                        continue
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        storage_total += usage.total
                        storage_used += usage.used
                        storage_free += usage.free
                    except Exception:
                        continue

            cached_storage_percent = (storage_used / storage_total * 100.0) if storage_total > 0 else 0
            cached_storage_total = storage_total
            cached_storage_used = storage_used
            cached_storage_free = storage_free
            last_storage_time = current_time

        # Medium metric: CPU Frequency - Refresh every 1.0 second
        if current_time - last_freq_time >= 1.0:
            freq = psutil.cpu_freq()
            cached_current_ghz = round(freq.current / 1000, 2) if freq else 0
            cached_max_ghz = round(freq.max / 1000, 2) if freq else 0
            last_freq_time = current_time

        # Medium metric: GPU Info - Refresh every 1.0 second
        if current_time - last_gpu_time >= 1.0:
            cached_gpu_info = get_gpu_info()
            last_gpu_time = current_time

        # Medium metric: Top Processes - Refresh every 2.0 seconds
        if current_time - last_processes_time >= 2.0:
            cached_processes = get_top_processes(5)
            last_processes_time = current_time

        # Ultra-slow metric: Top Largest Files - Refresh every 30.0 seconds to prevent 100% Disk Usage
        if current_time - last_files_time >= 30.0:
            cached_files = get_largest_files(5)
            last_files_time = current_time

        payload = {
            "id": HOSTNAME, "status": "online", "user": getpass.getuser(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "info": {
                "uptime": get_uptime(),
                "os": f"{platform.system()} {platform.release()}",
                "cpu_name": CPU_NAME
            },
            "network": {
                "down_mbps": round(max(0, down_mbps), 2),
                "traffic_in_gb": round(current_rx_bytes / (1024**3), 2),
                "latency_ms": cached_latency,
                "iface": INTERFACE_NAME,
                "ip": IP_ADDRESS,
                "mac": MAC_ADDRESS
            },
            "metrics": {
                "cpu": {
                    "percent": int(psutil.cpu_percent()),
                    "threads": CPU_THREADS,
                    "cores": CPU_CORES,
                    "ghz": cached_current_ghz,
                    "max_ghz": cached_max_ghz
                },
                "ram_percent": int(mem.percent),
                "ram": {
                    "used_gb": round(mem.used / (1024**3), 2),
                    "total_gb": round(mem.total / (1024**3), 1)
                },
                "storage": {
                    "total_gb": round(cached_storage_total / (1024**3), 1),
                    "used_gb": round(cached_storage_used / (1024**3), 1),
                    "free_gb": round(cached_storage_free / (1024**3), 1),
                    "percent": round(cached_storage_percent, 1)
                },
                "gpu": cached_gpu_info,
                "top_processes": cached_processes,
                "top_files": cached_files
            }
        }

        if not running:
            break  # Keluar sebelum sempat publish online lagi
        if mqtt_connected or client.is_connected():
            client.publish(TOPIC, json.dumps(payload), retain=True)
        else:
            print(f"[!] MQTT tidak terhubung, skip publish")
        print(f"[{payload['time']}] CPU: {payload['metrics']['cpu']['percent']}% | RAM: {payload['metrics']['ram_percent']}% | Net: {payload['network']['down_mbps']} Mbps")
    except Exception as e: print(f"Err: {e}")
    time.sleep(REFRESH_INTERVAL)

# ==================== CLEANUP SETELAH LOOP BERHENTI ====================
print("[*] Loop berhenti, mengirim status offline...")
try:
    offline_payload = json.dumps({"id": HOSTNAME, "status": "offline"})
    if client.is_connected():
        info = client.publish(TOPIC, offline_payload, retain=True)
        info.wait_for_publish(timeout=3)
        print("[✓] Status offline berhasil dikirim")
    else:
        print("[!] MQTT sudah disconnect, skip publish offline")
except Exception as e:
    print(f"[!] Gagal kirim offline: {e}")
finally:
    client.loop_stop()
    client.disconnect()
    print("[✓] Agent berhenti.")
    sys.exit(0)