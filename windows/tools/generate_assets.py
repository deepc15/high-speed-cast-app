import os
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFont

# Ensure assets directory in repo root
root_dir = Path(__file__).resolve().parent.parent.parent
assets_dir = root_dir / "assets"
assets_dir.mkdir(exist_ok=True)

# 1. Process and save the White, Gray, Green pictorial diagram
diagram_src = Path(r"C:\Users\cdeep\.gemini\antigravity-ide\brain\6dca2b84-8625-48af-af20-2fd05679fff4\hscast_white_green_diagram_1788454443479.jpg")
if diagram_src.exists():
    img = Image.open(diagram_src)
    # Save optimized PNG
    target_png = assets_dir / "hscast_overview.png"
    img.save(target_png, "PNG", optimize=True)
    print(f"Saved {target_png}")

# 2. Create the White, Gray & Green animated demo GIF
width, height = 640, 360
frames = []
total_frames = 36

# Theme Colors: White, Gray, and Green
bg_light = (248, 250, 252)       # Crisp off-white / light slate
panel_white = (255, 255, 255)    # Clean white cards
border_gray = (203, 213, 225)    # Slate-300 border
header_gray = (241, 245, 249)    # Slate-100 header
screen_bg = (255, 255, 255)      # White inner screen
grid_line = (235, 240, 246)      # Soft grid lines

accent_green = (16, 185, 129)    # Vibrant Emerald Green
accent_green_light = (209, 250, 229) # Soft mint green fill
accent_green_dark = (5, 150, 105) # Deep forest green

text_dark = (15, 23, 42)         # Slate-900
text_muted = (100, 116, 139)     # Slate-500
phone_border = (71, 85, 105)     # Dark slate gray frame

try:
    font_bold = ImageFont.truetype("arialbd.ttf", 16)
    font_title = ImageFont.truetype("arialbd.ttf", 20)
    font_sm = ImageFont.truetype("arial.ttf", 12)
    font_mono = ImageFont.truetype("consola.ttf", 11)
except Exception:
    font_bold = ImageFont.load_default()
    font_title = font_bold
    font_sm = font_bold
    font_mono = font_bold

for i in range(total_frames):
    img = Image.new("RGB", (width, height), bg_light)
    draw = ImageDraw.Draw(img)
    
    # Subtle background grid
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=grid_line, width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=grid_line, width=1)
        
    t = i / total_frames
    phase = (math.sin(t * 2 * math.pi) + 1) / 2
    
    # --- Top Bar (App Window header) ---
    draw.rounded_rectangle([(16, 12), (width - 16, 46)], radius=6, fill=panel_white, outline=border_gray, width=1)
    draw.ellipse([(28, 25), (36, 33)], fill=(239, 68, 68))
    draw.ellipse([(42, 25), (50, 33)], fill=(234, 179, 8))
    draw.ellipse([(56, 25), (64, 33)], fill=accent_green)
    
    draw.text((78, 21), "HSCast Studio — High-Speed Screen Casting", fill=text_dark, font=font_bold)
    
    # Live stats badge (right)
    fps_val = int(59 + math.sin(i * 0.8) * 1.5)
    rtt_val = int(7 + math.sin(i * 0.5) * 1.5)
    status_text = f"LIVE: {fps_val} FPS | {rtt_val}ms RTT | D3D11"
    draw.rounded_rectangle([(width - 235, 18), (width - 26, 40)], radius=4, fill=accent_green_light, outline=accent_green, width=1)
    draw.text((width - 225, 23), status_text, fill=accent_green_dark, font=font_mono)
    
    # --- Left: Android Phone Mockup ---
    phone_x, phone_y = 50, 60
    phone_w, phone_h = 150, 275
    # Outer frame
    draw.rounded_rectangle([(phone_x, phone_y), (phone_x + phone_w, phone_y + phone_h)], radius=18, fill=(241, 245, 249), outline=phone_border, width=2)
    # Screen inner
    screen_x, screen_y = phone_x + 8, phone_y + 12
    screen_w, screen_h = phone_w - 16, phone_h - 24
    draw.rounded_rectangle([(screen_x, screen_y), (screen_x + screen_w, screen_y + screen_h)], radius=12, fill=screen_bg, outline=border_gray, width=1)
    
    # Notch/Camera
    draw.ellipse([(phone_x + phone_w // 2 - 4, phone_y + 16), (phone_x + phone_w // 2 + 4, phone_y + 24)], fill=(148, 163, 184))
    
    # Android App content
    draw.text((screen_x + 12, screen_y + 18), "HSCast Mobile", fill=accent_green_dark, font=font_bold)
    draw.rounded_rectangle([(screen_x + 12, screen_y + 42), (screen_x + screen_w - 12, screen_y + 76)], radius=6, fill=header_gray, outline=border_gray, width=1)
    draw.text((screen_x + 18, screen_y + 46), "Stream Status", fill=text_muted, font=font_sm)
    draw.text((screen_x + 18, screen_y + 59), "Active (Sending)", fill=accent_green_dark, font=font_mono)
    
    # Animated green chart/bars inside phone
    base_y = screen_y + 180
    for bar_idx in range(6):
        bx = screen_x + 14 + bar_idx * 18
        bar_h = int(25 + 30 * math.sin(t * 2 * math.pi + bar_idx * 0.7))
        by = base_y - bar_h
        draw.rounded_rectangle([(bx, min(by, base_y)), (bx + 12, max(by, base_y))], radius=2, fill=accent_green)
        
    # Animated touch gesture ripple on phone
    touch_x = screen_x + int(40 + 50 * phase)
    touch_y = screen_y + int(90 + 40 * math.sin(t * 2 * math.pi))
    ripple_r = int((i % 12) * 2.2)
    draw.ellipse([(touch_x - ripple_r, touch_y - ripple_r), (touch_x + ripple_r, touch_y + ripple_r)], outline=accent_green, width=2)
    draw.ellipse([(touch_x - 3, touch_y - 3), (touch_x + 3, touch_y + 3)], fill=accent_green_dark)
    
    # --- Middle: Bidirectional Stream Arrows (Green & Gray) ---
    mid_x1, mid_x2 = phone_x + phone_w + 20, width - 260
    arrow_y1 = 145
    arrow_y2 = 215
    
    # Top arrow: Video stream -> PC
    draw.line([(mid_x1, arrow_y1), (mid_x2, arrow_y1)], fill=border_gray, width=2)
    for p_offset in range(3):
        packet_pos = mid_x1 + int(((t + p_offset * 0.33) % 1.0) * (mid_x2 - mid_x1))
        draw.rounded_rectangle([(packet_pos - 6, arrow_y1 - 5), (packet_pos + 6, arrow_y1 + 5)], radius=3, fill=accent_green)
    draw.polygon([(mid_x2, arrow_y1 - 6), (mid_x2 + 10, arrow_y1), (mid_x2, arrow_y1 + 6)], fill=accent_green)
    draw.text((mid_x1 + 10, arrow_y1 - 18), "60 FPS Stream", fill=accent_green_dark, font=font_mono)
    
    # Bottom arrow: Control events <- PC
    draw.line([(mid_x2 + 10, arrow_y2), (mid_x1, arrow_y2)], fill=border_gray, width=2)
    for p_offset in range(3):
        packet_pos = mid_x2 + 10 - int(((t + p_offset * 0.33) % 1.0) * (mid_x2 - mid_x1))
        draw.rounded_rectangle([(packet_pos - 6, arrow_y2 - 5), (packet_pos + 6, arrow_y2 + 5)], radius=3, fill=text_muted)
    draw.polygon([(mid_x1, arrow_y2 - 6), (mid_x1 - 10, arrow_y2), (mid_x1, arrow_y2 + 6)], fill=text_muted)
    draw.text((mid_x1 + 15, arrow_y2 + 8), "Mouse & Touch Control", fill=text_dark, font=font_mono)

    # --- Right: Mirrored PC Window ---
    pc_x, pc_y = width - 240, 60
    pc_w, pc_h = 215, 275
    draw.rounded_rectangle([(pc_x, pc_y), (pc_x + pc_w, pc_y + pc_h)], radius=10, fill=panel_white, outline=border_gray, width=2)
    # Titlebar inside PC
    draw.rounded_rectangle([(pc_x, pc_y), (pc_x + pc_w, pc_y + 26)], radius=6, fill=header_gray)
    draw.text((pc_x + 10, pc_y + 6), "Mirrored Phone Display", fill=text_dark, font=font_sm)
    
    # Screen inner mirror
    m_x, m_y = pc_x + 12, pc_y + 34
    m_w, m_h = pc_w - 24, pc_h - 46
    draw.rectangle([(m_x, m_y), (m_x + m_w, m_y + m_h)], fill=screen_bg, outline=border_gray, width=1)
    
    # Synchronized mirrored content
    draw.text((m_x + 14, m_y + 10), "HSCast Mobile", fill=accent_green_dark, font=font_sm)
    m_base_y = m_y + 150
    for bar_idx in range(6):
        bx = m_x + 16 + bar_idx * 26
        bar_h = int(25 + 30 * math.sin(t * 2 * math.pi + bar_idx * 0.7))
        by = m_base_y - bar_h
        draw.rounded_rectangle([(bx, min(by, m_base_y)), (bx + 18, max(by, m_base_y))], radius=2, fill=accent_green)
        
    # Mirrored Mouse Cursor on PC
    cur_x = m_x + int(40 + 70 * phase)
    cur_y = m_y + int(70 + 40 * math.sin(t * 2 * math.pi))
    draw.polygon([(cur_x, cur_y), (cur_x + 10, cur_y + 10), (cur_x + 4, cur_y + 11), (cur_x + 8, cur_y + 18), (cur_x + 5, cur_y + 19), (cur_x + 1, cur_y + 12), (cur_x - 1, cur_y + 15)], fill=(255, 255, 255), outline=text_dark)

    # Control toolbar at bottom of PC mirror
    draw.rectangle([(m_x, m_y + m_h - 26), (m_x + m_w, m_y + m_h)], fill=header_gray)
    draw.text((m_x + 12, m_y + m_h - 19), "Back   Home   Recents   Lock", fill=text_muted, font=font_mono)

    frames.append(img)

# Save as optimized animated GIF
target_gif = assets_dir / "hscast_demo.gif"
frames[0].save(
    target_gif,
    save_all=True,
    append_images=frames[1:],
    duration=65,
    loop=0,
    optimize=True
)
print(f"Saved {target_gif} successfully!")
