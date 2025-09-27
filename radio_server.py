# radio_server.py
import asyncio
import aiohttp
from aiohttp import web
import socket
import threading
import time
from collections import deque
import json
import soundfile as sf
import numpy as np
import struct
import os



async def html_handler(request):
    html_file_path = os.path.join(
        os.path.dirname(__file__), 'static', 'index.html'
    )

    with open(html_file_path, 'r') as f:
        html_content = f.read()

    return web.Response(text=html_content, content_type='text/html')

def getTracks():
    print(os.listdir("audio"))
    return os.listdir("audio")

class RadioServer:
    def __init__(self, host='0.0.0.0', port=8080):
        self.host = host
        self.port = port
        self.clients = set()
        self.audio_buffer = deque(maxlen=1000)
        self.current_position = 0
        self.is_playing = True
        self.currentFilename = getTracks()[0]
        self.currentIndex = 0
        self.audioTask = asyncio.create_task(self.broadcast_audio(
                            self.read_mp3_chunks()
                        ))

    async def html_handler_next(self, request):
        self.nextSound()
        return web.json_response({ 'success': true })
    async def html_handler_previous(self, request):
        self.previousSound()
        return web.json_response({ 'success': true })

    def nextSound(self):
        try:
            self.currentIndex += 1
            self.currentFilename = getTracks()[self.currentIndex]
        except IndexError:
            self.currentIndex = 0
            self.currentFilename = getTracks()[self.currentIndex]

        self.audioTask.cancel()
        self.audioTask = asyncio.create_task(self.broadcast_audio(
            self.read_mp3_chunks()
        ))

    def previousSound(self):
        try:
            self.currentIndex -= 1
            if self.currentIndex < 0: raise IndexError("nu da")
            self.currentFilename = getTracks()[self.currentIndex]
        except IndexError:
            self.currentIndex = len(getTracks()) - 1
            self.currentFilename = getTracks()[self.currentIndex]

        self.audioTask.cancel()
        self.audioTask = asyncio.create_task(self.broadcast_audio(
            self.read_mp3_chunks()
        ))
    
    async def websocket_handler(self, request):
        """Обработчик WebSocket соединений"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        self.clients.add(ws)
        print(f"Новый клиент подключен. Всего клиентов: {len(self.clients)}")
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    print(f"Получено сообщение: {msg.data}")
                elif msg.type == web.WSMsgType.ERROR:
                    print(f"WebSocket ошибка: {ws.exception()}")
        
        finally:
            self.clients.remove(ws)
            print(f"Клиент отключен. Осталось клиентов: {len(self.clients)}")
        
        return ws
        
    def create_wav_header(self, sample_rate, channels, data_size):
        """Создание WAV заголовка"""
        byte_rate = sample_rate * channels * 2
        block_align = channels * 2
        
        header = b'RIFF'
        header += struct.pack('<L', 36 + data_size)
        header += b'WAVE'
        header += b'fmt '
        header += struct.pack('<L', 16)
        header += struct.pack('<H', 1)  # PCM format
        header += struct.pack('<H', channels)
        header += struct.pack('<L', sample_rate)
        header += struct.pack('<L', byte_rate)
        header += struct.pack('<H', block_align)
        header += struct.pack('<H', 16)  # bits per sample
        header += b'data'
        header += struct.pack('<L', data_size)
        
        return header
    
    async def read_mp3_chunks(self, chunk_duration=3.0):
        """Чтение MP3 и преобразование в WAV чанки с заголовком"""
        try:
            data, sample_rate = sf.read(r"audio/" + self.currentFilename, dtype='float32')
            channels = 1 if len(data.shape) == 1 else data.shape[1]
            last = self.currentFilename
            flag = False
            
            chunk_size = int(chunk_duration * sample_rate)
            
            for i in range(0, len(data), chunk_size):
                if last == self.currentFilename:
                    chunk = data[i:i + chunk_size]
                    
                    # Преобразование в 16-bit PCM
                    chunk_int16 = (chunk * 32767).astype(np.int16)
                    audio_data = chunk_int16.tobytes()
                    
                    # Создание WAV заголовка для чанка
                    data_size = len(audio_data)
                    wav_header = self.create_wav_header(sample_rate, channels, data_size)
                    
                    # Полный WAV файл в памяти
                    full_wav = wav_header + audio_data
                    
                    yield full_wav, sample_rate, channels
                    print("sended chunk")
                    
                    await asyncio.sleep(chunk_duration)
                else:
                    break; flag = True

            if flag==False: self.nextSound()
                    
        except Exception as e:
            print(f"Ошибка: {e}")
    
    async def broadcast_audio(self, audio_generator):
        """Трансляция аудио всем подключенным клиентам"""
        async for audio_data, sample_rate, channels in audio_generator:
            if audio_data is None:
                break
            
            # Отправка всем подключенным клиентам
            for client in list(self.clients):
                try:
                    await client.send_bytes(audio_data)
                    
                except Exception as e:
                    print(f"Ошибка отправки клиенту: {e}")
                    try:
                        self.clients.remove(client)
                    except: pass
            
            # Задержка для синхронизации с реальным временем
            # await asyncio.sleep(0.1)  # регулируйте по необходимости
    
    async def status_handler(self, request):
        """HTTP handler для статуса сервера"""
        return web.json_response({
            'status': 'online',
            'clients': len(self.clients),
            'buffer_size': len(self.audio_buffer),
            'position': self.current_position
        })
    
    async def start_server(self):
        """Запуск сервера"""
        app = web.Application()
        app.router.add_get('/ws', self.websocket_handler)
        app.router.add_get('/status', self.status_handler)
        app.router.add_get('/', html_handler)
        app.router.add_get('/next', self.html_handler_next)
        app.router.add_get('/previous', self.html_handler_previous)
        app.router.add_static('/', 'static/')
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        
        print(f"Радио сервер запущен на http://{self.host}:{self.port}")
        print("Статус доступен по http://localhost:8080/status")
        
        # Бесконечный цикл
        await asyncio.Future()

async def main():
    server = RadioServer()
    await server.start_server()

if __name__ == '__main__':
    asyncio.run(main())
