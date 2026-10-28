"""Worker agent v2. Job padhta hai -> kaam karta hai (code / writing / video-script) ->
tests + 2 baar review -> aapko FILE + CLIENT KA LINK bhejta hai.
Client se baat, delivery aur paisa: sab aap karte ho. Agent kisi se contact nahi karta."""
import os, sys, json, subprocess, tempfile, pathlib, requests

KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
TOPIC = os.environ["NTFY_TOPIC"]
JOB = os.environ["JOB_TEXT"][:8000]
JOB_URL = os.environ.get("JOB_URL", "").strip() or "(link nahi diya)"
RUN_URL = "{}/{}/actions/runs/{}".format(os.environ.get("GITHUB_SERVER_URL", ""),
                                          os.environ.get("GITHUB_REPOSITORY", ""),
                                          os.environ.get("GITHUB_RUN_ID", ""))
MAX_ATTEMPTS = 3
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)


def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for _ in range(2):
        r = requests.post(url, params={"key": KEY}, timeout=120, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}})
        try:
            return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e:
            print("parse fail, retry:", e)
    raise RuntimeError("Gemini ka jawab samajh nahi aaya")


def notify(title, msg):
    requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode("utf-8"),
                  headers={"Title": title.encode("utf-8"), "Click": RUN_URL})


def run_tests(solution, tests):
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "solution.py").write_text(solution)
        pathlib.Path(d, "test_solution.py").write_text(tests)
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"],
                               cwd=d, env={"PATH": os.environ["PATH"], "HOME": d},  # secrets nahi
                               capture_output=True, text=True, timeout=90)
            return p.returncode == 0, (p.stdout + p.stderr)[-3000:]
        except subprocess.TimeoutExpired:
            return False, "tests 90 second me khatam nahi hue (timeout)"


def review_code(checklist, solution, tests):
    return gemini(f"""Sakht code reviewer ho. Checklist ka HAR point solution me poora hua ya nahi, ek-ek karke jaanch.
CHECKLIST: {json.dumps(checklist)}
SOLUTION:\n{solution}\nTESTS:\n{tests}
Sirf JSON: {{"all_met": true/false, "gaps": ["jo point poora nahi hua"]}}""")


def review_text(req, text):
    return gemini(f"""Sakht editor ho. Dekho ki deliverable client ki HAR requirement poori karta hai ya nahi
(length, tone, topic, format, keywords), aur ye bhi: koi jhoota/banaya hua fact, statistic ya link to nahi,
generic ya copy-paste jaisa to nahi.
CHECKLIST: {json.dumps(req['checklist'])}
DELIVERABLE:\n{text}
Sirf JSON: {{"all_met": true/false, "gaps": ["jo kami hai"]}}""")


def work_code(req):
    feedback, solution, tests, ok, log = "", "", "", False, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out = gemini(f"""Python solution aur pytest tests likho.
CHECKLIST: {json.dumps(req['checklist'])}
PICHLI DIKKAT: {feedback or 'koi nahi'}
PICHLA SOLUTION: {solution or 'koi nahi'}
PICHLE TESTS: {tests or 'koi nahi'}
Rules: solution 'solution.py' me, tests 'test_solution.py' me ('from solution import ...').
Tests checklist ke har point ko cover karein, sirf solution dekhkar na likho.
Sirf standard library + pytest. Network ka istemal nahi.
Sirf JSON: {{"solution": "...", "tests": "..."}}""")
        solution, tests = out["solution"], out["tests"]
        ok, log = run_tests(solution, tests)
        if not ok:
            feedback = "Tests fail hue:\n" + log
            continue
        gaps = []
        for _ in range(2):
            gaps += review_code(req["checklist"], solution, tests).get("gaps", [])
        if gaps:
            ok, feedback = False, "Review me ye poore nahi mile: " + "; ".join(sorted(set(gaps)))
            continue
        break
    (OUT / "solution.py").write_text(solution)
    (OUT / "test_solution.py").write_text(tests)
    return ok, feedback, log


def work_text(req, kind):
    feedback, text, ok = "", "", False
    note = ("Ye VIDEO ka script/shot-list/captions hai; asli editing aapko khud karni hai."
            if kind == "video" else "")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out = gemini(f"""Client ke liye deliverable taiyaar karo ({'video script + shot list + captions' if kind == 'video' else 'writing/copy'}).
JOB:\n{JOB}
CHECKLIST: {json.dumps(req['checklist'])}
PICHLI DIKKAT: {feedback or 'koi nahi'}
PICHLA DRAFT: {text or 'koi nahi'}
Rules: koi jhoota fact, statistic, quote ya link mat banao. Jo jaankari nahi hai wahan [CLIENT SE POOCHO] likho.
Insaani, specific bhasha; generic filler nahi. Client ki bhasha me likho.
Sirf JSON: {{"text": "poora deliverable markdown me"}}""")
        text = out["text"]
        gaps = []
        for _ in range(2):
            gaps += review_text(req, text).get("gaps", [])
        if not gaps:
            ok = True
            break
        feedback = "Review me ye kami mili: " + "; ".join(sorted(set(gaps)))
    (OUT / "deliverable.md").write_text(text)
    return ok, feedback, note


def main():
    req = gemini(f"""Ye freelance job padho. Client ko exactly kya chahiye?
JOB:\n{JOB}
Sirf JSON: {{"summary": "ek line",
 "checklist": ["har requirement ek point, jaanchne layayak"],
 "ambiguities": ["jo saaf nahi hai"],
 "job_type": "code" ya "writing" ya "video" ya "other"}}
code = Python script jiska result tests se jaancha ja sake. writing = copy/article/content/translation.
video = video editing. other = baaki sab (design, calls, kuch jo online/offline insaan chahiye).""")
    (OUT / "requirements.json").write_text(json.dumps(req, indent=2, ensure_ascii=False))
    kind = req.get("job_type", "other")
    ask = "\n- ".join(req.get("ambiguities", []) or ["koi nahi"])

    if kind == "code":
        ok, feedback, log = work_code(req); note = ""
    elif kind in ("writing", "video"):
        ok, feedback, note = work_text(req, kind); log = ""
    else:
        notify("SKIP: ye kaam agent nahi kar sakta",
               f"{req.get('summary','')}\n\nJob ka link: {JOB_URL}")
        return

    (OUT / "report.md").write_text(
        f"# {'READY' if ok else 'FAILED'}\n\nClient link: {JOB_URL}\n\n{req.get('summary','')}\n\n"
        "## Checklist\n- " + "\n- ".join(req["checklist"]) + f"\n\n## Client se poochna\n- {ask}\n\n{note}\n\n{log}\n")

    if ok:
        notify("READY: kaam taiyaar, aap check karke bhejo",
               f"{req.get('summary','')}\n\nCLIENT KA LINK: {JOB_URL}\n\n"
               f"Do baar review pass. Files: Actions run ke Artifacts me.\n{note}\n"
               f"Bhejne se pehle khud padho/chalao.\nClient se poochna:\n- {ask}")
    else:
        notify("FAILED: 3 koshish me pura nahi hua",
               f"{req.get('summary','')}\n\nCLIENT KA LINK: {JOB_URL}\n\nAkhri dikkat:\n{feedback[:800]}")


if __name__ == "__main__":
    main()
