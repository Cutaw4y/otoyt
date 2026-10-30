"""Instagram uçtan uca test: küçük bir dikey video üretir, Meta'ya yükler, işlenmesini bekler, YAYINLAMAZ."""
import subprocess
import sys
import publish

subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x1a1410:s=1080x1920:r=30:d=5",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", "test.mp4"], check=True)
try:
    cid, msg = publish.instagram_reel("test.mp4", "test #gölgedefter", ai_label=True, dry=True, poll_every=10)
    print(f"::notice title=Instagram uçtan uca test::BAŞARILI — video yüklendi ve işlendi ({msg})")
except Exception as e:
    print(f"::error title=Instagram uçtan uca test::{str(e)[:300]}")
    sys.exit(1)
