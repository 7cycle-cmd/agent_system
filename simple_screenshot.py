from mss import MSS
from mss.tools import to_png
import time, os


def capture_screen(save_path):
    with MSS() as sct:
        main_mon = sct.monitors[1]      # 1 号显示器（0 号是所有屏幕拼起来的虚拟屏）
        shot = sct.grab(main_mon)       # 返回 ScreenShot 对象，没有 .save()
        to_png(shot.rgb, shot.size, output=save_path)  # 由 mss 负责编码成 PNG
    return save_path


if __name__ == "__main__":
    fn = f"hb_{int(time.time())}.png"
    p = capture_screen(fn)
    print("输出文件：", os.path.abspath(p))