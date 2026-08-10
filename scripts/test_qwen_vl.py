import base64, struct, zlib, json, urllib.request, sys
sys.path.insert(0, 'E:/hermes-opc')

def png_rgb(w, h, r, g, b):
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    row = b'\x00' + bytes([r, g, b, 255]) * w
    raw = row * h
    idat = zlib.compress(raw)
    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')

b64 = base64.b64encode(png_rgb(64, 64, 255, 0, 0)).decode()
key = [l.split('=', 1)[1].strip().strip('"') for l in open('E:/hermes-opc/.env', encoding='utf-8') if l.startswith('DASHSCOPE_API_KEY=')][0]
body = json.dumps({
    "model": "qwen-vl-plus",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "这是什么颜色的图片？用中文回答"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}
    ]}],
    "max_tokens": 30
}).encode()
req = urllib.request.Request(
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
    print('✅ qwen-vl-plus 视觉识别成功:')
    print('  ', resp['choices'][0]['message']['content'][:100])
except Exception as e:
    print('失败:', e)
