"""Browser walk-through of the demo story (Playwright for Python).

Start the API first (scripts/run_demo.sh), then:  python scripts/e2e_demo.py
Screenshots land in demos/screenshots/. Needs: pip install playwright && playwright install chromium
"""
import os
from playwright.sync_api import sync_playwright
OUT = os.environ.get("CARELENS_SHOTS", os.path.join(os.path.dirname(__file__), "..", "demos", "screenshots"))
BASE = os.environ.get("CARELENS_URL", "http://localhost:8000/")
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width":1240,"height":1000})
    errs=[]
    pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE); pg.wait_for_selector(".phone-date")
    pg.click(".demobar-summary")  # open the demo drawer (people + clock)
    pg.click("text=Restart the day at 7:55 AM"); pg.wait_for_selector(".daystrip"); pg.wait_for_timeout(800)
    pg.click("text=+10 min"); pg.wait_for_selector(".notice-reminder", timeout=10000)
    pg.screenshot(path=f"{OUT}/1-reminder.png")
    for i,q in enumerate(["What should I do this morning?","What is this medicine used for?","I took it.","Remind me before my electricity bill is due."]):
        pg.click(f"button.suggestion:has-text('{q}')"); pg.wait_for_function("q => document.querySelector('.answer-question')?.textContent.includes(q)", arg=q); pg.wait_for_timeout(400)
        print(q, "->", pg.inner_text(".answer-text")[:110])
    pg.locator(".phone-body").evaluate("e => e.scrollTop = 0"); pg.screenshot(path=f"{OUT}/2-after-story.png")
    pg.locator(".phone-body").evaluate("e => e.scrollTop = e.scrollHeight"); pg.screenshot(path=f"{OUT}/3-answer.png")
    # refusal
    pg.fill("#ask","Can I take two today?"); pg.click("button:has-text('Ask')"); pg.wait_for_selector(".answer-refusal"); print("refusal:", pg.inner_text(".answer-text")[:80])
    pg.screenshot(path=f"{OUT}/4-refusal.png")
    # offline: local reminder for evening dose
    pg.check("text=Cut the phone’s network"); pg.wait_for_selector(".notice-offline")
    for _ in range(5): pg.click("text=+2 hours"); pg.wait_for_timeout(700)
    pg.wait_for_selector("text=Sent by this phone while offline", timeout=8000)
    pg.locator(".phone-body").evaluate("e => e.scrollTop = 0")
    pg.locator("article:has-text('metformin') >> text=I took it").click(); pg.wait_for_selector("text=Saved on this phone")
    pg.screenshot(path=f"{OUT}/5-offline.png")
    pg.uncheck("text=Cut the phone’s network"); pg.wait_for_timeout(5000)
    print("offline banner gone:", pg.locator(".notice-offline").count()==0, "| pending sync left:", pg.locator("text=Saved on this phone").count())
    # bills + sharing
    pg.click(".tabs >> text=Bills"); pg.wait_for_selector("text=Your bills"); pg.screenshot(path=f"{OUT}/6-bills.png")
    pg.click(".tabs >> text=Sharing"); pg.wait_for_selector("text=Who can see your list"); pg.screenshot(path=f"{OUT}/7-sharing.png")
    # caregiver
    pg.click(".personas >> text=Daniel Alvarez"); pg.wait_for_selector(".table"); pg.screenshot(path=f"{OUT}/8-caregiver.png")
    print("daniel:", pg.inner_text(".table tbody").replace("\n"," ")[:300])
    pg.click(".personas >> text=Priya Shah"); pg.wait_for_selector("text=Nothing is shared with you")
    pg.click(".personas >> text=Sam Okafor"); pg.wait_for_selector("text=Waiting for verification"); pg.screenshot(path=f"{OUT}/9-verifier.png")
    pg.click("text=Verify record and start reminders"); pg.wait_for_timeout(800); print("pending left:", pg.locator("text=Waiting for verification").count())
    # mobile
    m = b.new_page(viewport={"width":390,"height":844}); m.goto(BASE); m.wait_for_selector(".daystrip"); m.screenshot(path=f"{OUT}/10-mobile.png")
    print("console errors:", errs)
    b.close()
