"""PNG 프레임 디렉터리를 반복 재생하는 GIF 하나로 이어 붙인다. Pillow 가 필요하다.

사용법: python3 docs/make_gif.py <프레임 디렉터리> <출력 .gif> <프레임별 ms, 쉼표 구분>
프레임은 파일 이름 순서로 붙고, 모든 프레임이 첫 프레임의 팔레트를 함께 쓴다.
"""
import sys
from pathlib import Path

from PIL import Image


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    frames_dir, out, ms = Path(sys.argv[1]), Path(sys.argv[2]), [int(x) for x in sys.argv[3].split(",")]
    files = sorted(frames_dir.glob("*.png"))
    if len(files) != len(ms):
        sys.exit(f"프레임 {len(files)}장과 시간 {len(ms)}개가 맞지 않습니다")
    rgb = [Image.open(f).convert("RGB") for f in files]
    sheet = Image.new("RGB", (rgb[0].width, rgb[0].height * len(rgb)))
    for k, im in enumerate(rgb):
        sheet.paste(im, (0, im.height * k))
    palette = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in rgb]
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True, disposal=1)


if __name__ == "__main__":
    main()
