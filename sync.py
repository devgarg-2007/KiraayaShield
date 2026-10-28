import subprocess
import sys

msg = sys.argv[1] if len(sys.argv) > 1 else "feat: update KiraayaShield Pro full-stack platform"
print("🔄 Staging all files...")
subprocess.run(["git", "add", "."], check=False)
print(f"📝 Committing with message: '{msg}'...")
subprocess.run(["git", "commit", "-m", msg], check=False)
print("🚀 Pushing to GitHub...")
subprocess.run(["git", "push"], check=False)
print("✅ Done!")
