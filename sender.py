import socket
import struct
import webrtcvad
import numpy as np
import pyaudio
import time
import sounddevice as sd


# ============================================================
#                     PARAMETRI GLOBALI
# ============================================================

RATE = 16000                 # sample rate
FRAME_MS = 30                # durata frame in ms
FRAME_SIZE = int(RATE * FRAME_MS / 1000)   # 480 campioni a 16 kHz
BYTES_PER_SAMPLE = 2         # int16
CHUNK_BYTES = FRAME_SIZE * BYTES_PER_SAMPLE

SERVER_IP = "127.0.0.1"      # IP del receiver (WSL)
SERVER_PORT = 50007

# VAD aggressivo (0 = permissivo, 3 = molto aggressivo)
VAD_AGGRESSIVENESS = 0

# almeno N frame consecutivi marcati come speech
MIN_CONSEC_SPEECH_FRAMES = 0 #5   # 5 * 30 ms = 150 ms

# soglia minima di energia RMS per considerare davvero speech
MIN_RMS = 0.001 #0.003[insert many spurious] #0.01[insert low spurious]

GAIN = 5#0

# ============================================================
#                     INIZIALIZZAZIONE VAD
# ============================================================

vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

# Stato del parlato
speech_run = 0
in_speech = False

# ============================================================
#                     INIZIALIZZAZIONE AUDIO
# ============================================================

pa = pyaudio.PyAudio()

stream = pa.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=RATE,
    input=True,
    frames_per_buffer=FRAME_SIZE,
)


def get_default_input_device():
    idx = sd.default.device[0]  # input device index
    info = sd.query_devices(idx)
    return idx, info

def classify_device(info):
    name = info["name"].lower()
    channels = info["max_input_channels"]

    if "array" in name or channels > 1:
        return "microphone_array"
    else:
        return "headset"

def get_gain_for_device(device_type):
    if device_type == "microphone_array":
        return 5
    if device_type == "headset":
        return 1
    return 1.5  # fallback


def get_dynamic_gain():
    idx, info = get_default_input_device()
    device_type = classify_device(info)
    gain = get_gain_for_device(device_type)

    print("Default device:", info["name"])
    print("Type:", device_type)
    print("Using gain:", gain)

    return gain


GAIN=get_dynamic_gain()


# ============================================================
#                     CONNESSIONE SOCKET
# ============================================================

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

print(f"[sender] Connecting to {SERVER_IP}:{SERVER_PORT} ...")
sock.connect((SERVER_IP, SERVER_PORT))
print("[sender] Connected.")

# ============================================================
#                     LOOP PRINCIPALE
# ============================================================

try:
    while True:
        # Leggi un frame da 30 ms
        data = stream.read(FRAME_SIZE, exception_on_overflow=False)
        if not data:
            continue

        # -----------------------------
        # 1) VAD WebRTC
        # -----------------------------
        is_speech_vad = vad.is_speech(data, RATE)

        # -----------------------------
        # 2) Energia RMS
        # -----------------------------
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        pcm = pcm * GAIN
        rms = np.sqrt(np.mean(pcm**2) + 1e-8)

        # doppio filtro: VAD + energia
        is_speech = is_speech_vad and (rms > MIN_RMS)

        # -----------------------------
        # 3) Frame consecutivi
        # -----------------------------
        if is_speech:
            speech_run += 1
        else:
            speech_run = 0

        # Entrata nel parlato
        if not in_speech and speech_run >= MIN_CONSEC_SPEECH_FRAMES:
            in_speech = True

        # Uscita dal parlato
        if in_speech and not is_speech:
            in_speech = False

        # -----------------------------
        # 4) INVIO SU SOCKET
        # -----------------------------
        # Torna a int16 per invio
        pcm16 = (pcm * 32767).astype(np.int16)
        payload = pcm16.tobytes()                         
        
        if in_speech:
            # invio frame come speech
            print("IS_SPEECH")
            #length = len(data)
            length = len(payload)
            sock.sendall(struct.pack(">I", length))
            #sock.sendall(data)
            sock.sendall(payload)            
        else:
            print("HEART_BEAT")            
            # heartbeat
            sock.sendall(struct.pack(">I", 0))

except KeyboardInterrupt:
    print("\n[sender] Stopping...")

finally:
    stream.stop_stream()
    stream.close()
    pa.terminate()
    sock.close()
    print("[sender] Closed.")
