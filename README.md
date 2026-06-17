# 🖥️ Lab Monitoring System

Sistem pemantauan laboratorium komputer berbasis web dengan arsitektur agen-server menggunakan protokol MQTT.

## 📋 Deskripsi

Sistem ini terdiri dari dua komponen utama:

### 1. Agent (`agent.py`) - Client Side
Script yang berjalan di setiap komputer klien untuk mengumpulkan metrik performa secara real-time:
- **CPU**: Utilisasi, frekuensi, core/thread count, CPU name
- **RAM**: Penggunaan memori (GB)
- **Storage**: Kapasitas penyimpanan (GB)
- **GPU**: Monitoring kartu grafis (NVIDIA, AMD, Intel)
- **Network**: Latency (ping), bandwidth, interface info
- **Top Processes**: 5 proses teratas berdasarkan CPU usage
- **Top Files**: 5 berkas terbesar di sistem

**Update interval**: 0.1 detik (100ms) untuk responsivitas tinggi

### 2. Dashboard Server (`main.py`) - Server Side
Server web berbasis Flask yang menampilkan dashboard monitoring:
- **Web Interface**: Akses via browser di `http://localhost:5000`
- **Real-time Updates**: Auto-refresh setiap 5 detik
- **Soft Purple Theme**: Desain elegan dengan gradient ungu
- **Multi-Computer Support**: Menampilkan semua komputer yang menjalankan agent
- **Status Monitoring**: Online/Offline status dengan indikator visual
- **API Endpoint**: `/api/computers` untuk akses data JSON

## 🚀 Instalasi

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Pastikan MQTT broker berjalan di `192.168.1.11:1883`

## 📖 Cara Penggunaan

### 1. Jalankan Dashboard Server (di server/admin):
```bash
python main.py
```

Server akan berjalan di `http://localhost:5000`

### 2. Jalankan Agent (di setiap komputer klien):
```bash
python agent.py
```

Agent akan mengirim data ke broker MQTT dan otomatis muncul di dashboard.

## ⚙️ Konfigurasi

### MQTT Broker
- **Host**: `192.168.1.23`
- **Port**: `1883`
- **Topic**: `lab/monitoring/{hostname}`

### Dashboard Server
- **Port**: `5000`
- **URL**: `http://localhost:5000`
- **API**: `http://localhost:5000/api/computers`

### Custom Configuration
Edit bagian konfigurasi di `agent.py` atau `main.py` untuk mengubah:
- `BROKER_URL`: Alamat MQTT broker
- `PORT`: Port MQTT
- `DASHBOARD_PORT`: Port dashboard server

## 🎨 Fitur Dashboard

- **Web-based Interface**: Akses dari browser apapun
- **Responsive Design**: Support desktop dan mobile
- **Real-time Metrics**: CPU name, RAM (GB), Storage (GB), Network
- **Visual Indicators**: Progress bars dan status colors
- **Summary Cards**: Total computers, online/offline count, avg CPU
- **Auto-refresh**: Update otomatis setiap 5 detik
- **API Endpoint**: Data JSON untuk integrasi

## 📊 Metrik yang Dimonitor

| Metrik | Interval Update | Keterangan |
|--------|----------------|------------|
| CPU Usage | 0.1s | Persentase utilisasi + CPU name |
| RAM | 0.1s | Total (GB) + penggunaan |
| Storage | 10.0s | Total (GB) + penggunaan |
| Network | 0.1s | Bandwidth + latency |
| GPU Info | 1.0s | Utilisasi & temperatur |
| Processes | 2.0s | Top 5 by CPU |
| Large Files | 30.0s | Top 5 files >1MB |

## 🔧 Command Execution (Remote Control)

Agent mendukung command execution via MQTT untuk admin:
- `tasklist` - List proses berjalan
- `ipconfig` - Info jaringan
- `whoami` - User saat ini
- `systeminfo` - Info sistem
- `taskkill` - Hentikan proses (butuh parameter `process_name`)
- `shutdown` - Matikan komputer
- `restart` - Restart komputer
- `cancel_shutdown` - Batalkan shutdown

Topic command: `lab/command/{hostname}`

## 📝 Dependencies

- `paho-mqtt`: MQTT client library
- `psutil`: System monitoring
- `flask`: Web framework
- `pynvml`: NVIDIA GPU monitoring (optional)

## 🌐 Network Architecture

```
[Client 1] --MQTT--> [Broker 192.168.1.11] <--MQTT-- [Client 2]
                            |
                            v
                    [Dashboard Server]
                            |
                            v
                    [Web Browser:5000]
```

## 💡 Cara Kerja

1. **Agent** berjalan di setiap komputer lab, mengumpulkan data sistem setiap 0.1 detik
2. Data dikirim ke **MQTT Broker** di topic `lab/monitoring/{hostname}`
3. **Dashboard Server** subscribe ke semua topic monitoring
4. Server menyimpan data terbaru dari setiap komputer
5. Saat browser mengakses dashboard, server menampilkan data dalam format web
6. Dashboard auto-refresh setiap 5 detik untuk menampilkan data terbaru

## 📄 License

Free to use for educational purposes.