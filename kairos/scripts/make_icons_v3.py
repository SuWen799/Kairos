# -*- coding: utf-8 -*-
"""v3 星芒 Logo 落地：从 2048 图形标生成 Electron 全套图标资产 + 前端内嵌用小图。

产出：
  desktop/resources/icon.ico   (256/128/64/48/32/16)
  desktop/resources/icon.png   (512, linux/mac 用)
  desktop/resources/tray.png   (32, 托盘)
  web/src/assets/logo-mark.png  (256, UI 内嵌)
  web/src/assets/logo-lockup.png (横版组合原尺寸压缩到 1024 宽)
"""
import os
from PIL import Image

SRC = r"C:/Users/<user>\Desktop\秋招文件\项目源码\Kairos\UI设计\UI与Logo设计产出-v3星芒\Logo系列"
KAIROS = r"C:/Users/<user>\Desktop\秋招文件\项目源码\Kairos"

mark_src = os.path.join(SRC, "Kairos-Logo-星芒图形标-1x1.png")
lockup_src = os.path.join(SRC, "Kairos-Logo-星芒横版组合-3x1.png")

res_dir = os.path.join(KAIROS, "desktop", "resources")
assets_dir = os.path.join(KAIROS, "web", "src", "assets")
os.makedirs(res_dir, exist_ok=True)
os.makedirs(assets_dir, exist_ok=True)

mark = Image.open(mark_src).convert("RGBA")
lockup = Image.open(lockup_src).convert("RGBA")
print("mark:", mark.size, "lockup:", lockup.size)

# 1) Windows ico：多尺寸
ico_path = os.path.join(res_dir, "icon.ico")
mark.resize((256, 256), Image.LANCZOS).save(
    ico_path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("OK", ico_path)

# 2) icon.png 512（linux/mac + builder 兜底）
mark.resize((512, 512), Image.LANCZOS).save(os.path.join(res_dir, "icon.png"))
print("OK", os.path.join(res_dir, "icon.png"))

# 3) 托盘 32px
mark.resize((32, 32), Image.LANCZOS).save(os.path.join(res_dir, "tray.png"))
print("OK", os.path.join(res_dir, "tray.png"))

# 4) 前端内嵌：图形标 256 + 横版组合 1024 宽
mark.resize((256, 256), Image.LANCZOS).save(os.path.join(assets_dir, "logo-mark.png"))
w = 1024
h = round(lockup.height * w / lockup.width)
lockup.resize((w, h), Image.LANCZOS).save(os.path.join(assets_dir, "logo-lockup.png"))
print("OK", assets_dir)
