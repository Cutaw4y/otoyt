"""Instagram uçtan uca test: dikey test videoları üretir (farklı sürelerde), Meta'ya yükler, işlenmesini bekler,
YAYINLAMAZ. Süreler IG_TEST_DURATIONS ile verilir (ör. "5" veya "30,39,45,55")."""
import os
import subprocess
import sys
import publish

fail = 0
for d in [int(x) for x in os.getenv("IG_TEST_DURATIONS", "5").split(",")]:
    f = f"test_{d}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x1a1410:s=1080x1920:r=30:d={d}",
                    "-f", "lavfi", "-i", f"sine=frequency=220:duration={d}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart", f], check=True)
    try:
        cid, msg = publish.instagram_reel(f, "test #gölgedefter", ai_label=True, dry=True, poll_every=10)
        print(f"::notice title=Instagram testi {d} sn::BAŞARILI ({msg})")
    except Exception as e:
        fail += 1
        print(f"::error title=Instagram testi {d} sn::{str(e)[:300]}")
sys.exit(1 if fail else 0)
