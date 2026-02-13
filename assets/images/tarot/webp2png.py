"""
将当前目录下所有 .webp 文件转换为 .png 格式。
依赖: pip install Pillow
"""

from pathlib import Path
from PIL import Image


def main():
    directory = Path(__file__).parent
    webp_files = list(directory.glob("*.webp"))

    if not webp_files:
        print("当前目录下没有找到 .webp 文件。")
        return

    print(f"找到 {len(webp_files)} 个 .webp 文件，开始转换...")

    for webp_path in webp_files:
        png_path = webp_path.with_suffix(".png")
        try:
            with Image.open(webp_path) as img:
                img.save(png_path, "PNG")
            print(f"  ✔ {webp_path.name} -> {png_path.name}")
        except Exception as e:
            print(f"  ✘ {webp_path.name} 转换失败: {e}")

    print("转换完成。")


if __name__ == "__main__":
    main()
