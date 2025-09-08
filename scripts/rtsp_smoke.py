import argparse
import time

from modules.capture.rtsp_ffmpeg import RtspFfmpegSource


def main() -> None:
    parser = argparse.ArgumentParser(description="RTSP smoke test")
    parser.add_argument("url", help="RTSP URL")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    args = parser.parse_args()
    src = RtspFfmpegSource(
        args.url,
        width=args.width or None,
        height=args.height or None,
    )
    src.open()
    start = time.time()
    frames = 0
    first_latency = None
    try:
        while frames < 100:
            frame = src.read(timeout=5)
            frames += 1
            if frames == 1:
                first_latency = src.first_frame_ms or int((time.time() - start) * 1000)
                print(f"FIRST_FRAME {first_latency}ms")
        dur = time.time() - start
        fps = frames / dur if dur > 0 else 0.0
        print(f"Captured {frames} frames in {dur:.2f}s ({fps:.2f} FPS)")
    finally:
        src.close()


if __name__ == "__main__":
    main()
