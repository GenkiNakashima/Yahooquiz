#!/usr/bin/env python3
"""
ファビコンをPNGからICO形式に変換するスクリプト
使用方法: python convert_favicon.py
"""

from PIL import Image
import os

def convert_png_to_ico():
    """PNG形式のfaviconをICO形式に変換"""
    input_path = 'static/images/aiquiz-favicon.png'
    output_path = 'static/images/aiquiz-favicon.ico'
    
    if not os.path.exists(input_path):
        print(f"エラー: {input_path} が見つかりません")
        return False
    
    try:
        # PNGファイルを開く
        img = Image.open(input_path)
        
        # ICO形式に変換（標準的なサイズ: 32x32, 16x16）
        # ブラウザのファビコンに最適なサイズは16x16, 32x32, 64x64など
        img_resized = img.resize((32, 32), Image.Resampling.LANCZOS)
        
        # ICO形式で保存
        img_resized.save(output_path, format='ICO')
        
        print(f"✅ 変換成功: {input_path} → {output_path}")
        print(f"ファイルサイズ: {os.path.getsize(output_path)} bytes")
        return True
        
    except Exception as e:
        print(f"❌ 変換失敗: {e}")
        return False

if __name__ == '__main__':
    convert_png_to_ico()
