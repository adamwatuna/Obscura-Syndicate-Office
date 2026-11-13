"""CEO presentation decks grounded only in recorded AI staff deliverables."""
import io
import re
from collections import defaultdict
from backend import meita_autowork

def excerpts(text,limit=4):
    lines=[]
    for line in str(text or "").splitlines():
        line=re.sub(r"^\s*(?:#{1,5}\s*|[-*•]\s*|\d+[.)]\s*)","",line).strip("*_ |")
        if len(line)<16:continue
        if len(line)>170:line=line[:169].rstrip()+"…"
        if line not in lines:lines.append(line)
        if len(lines)>=limit:break
    return lines or ([str(text)[:170]] if text else [])

def build(bundle):
    p=bundle["proposal"];jobs=bundle["jobs"];n=len(jobs)
    complete=sum(bool(j.get("result")) for j in jobs)
    needs=sum(j["status"] in ("Retry available","Needs verification") for j in jobs)
    progress=max(0,n-complete-needs)
    d=defaultdict(lambda:{"total":0,"delivered":0})
    for job in jobs:
        name=job["staff"].get("department") or "Other"
        d[name]["total"]+=1
        if job.get("result"):d[name]["delivered"]+=1
    slides=[
        {"kind":"cover","title":p["title"],"subtitle":"MEITA · CEO EXECUTIVE DELIVERY"},
        {"kind":"overview","title":"Executive overview",
         "subtitle":"Verified internal task counts — not business performance",
         "metrics":[{"label":"Assigned","value":n},{"label":"Delivered","value":complete},
                    {"label":"Processing","value":progress},{"label":"Attention","value":needs}],
         "bullets":excerpts(p["brief"],3)},
        {"kind":"chart","title":"Team delivery by department",
         "subtitle":"Actual delivered vs assigned AI tasks",
         "bars":[{"name":name,**counts} for name,counts in sorted(d.items())]},
    ]
    for job in jobs:
        have=bool(job.get("result"))
        slides.append({"kind":"work","title":job["title"],
            "subtitle":job["staff"].get("department",""),
            "owner":job["staff"].get("name","AI staff"),
            "status":job["status"],
            "bullets":excerpts(job.get("result") or job["brief"],4),
            "note":"From actual AI work; CEO should verify any factual claims." if have else
                   "Awaiting AI output; task instructions shown.",
            "available":have})
    slides.append({"kind":"close","title":"CEO review & next steps",
        "subtitle":"Internal briefing",
        "bullets":[f"{complete} of {n} AI deliverables received.",
                   "Review unfinished or failed items in Task History.",
                   "Verify facts, figures, dates and any external sources.",
                   "Publishing, payments and outreach require separate permissions."]})
    return {"proposal_id":p["id"],"title":p["title"],"slides":slides,
       "metrics":{"assigned":n,"delivered":complete,"processing":progress,
                  "needs_attention":needs},
       "reports":[{"title":j["title"],"status":j["status"],
                   "owner":j["staff"].get("name","AI staff"),
                   "department":j["staff"].get("department",""),
                   "content":j.get("result") or "", "sources":j.get("sources") or []}
                  for j in jobs]}

def from_db(db,proposal_id):
    return build(meita_autowork.output_bundle(db,proposal_id))

def as_pptx(deck):
    """Editable 16:9 PowerPoint with a genuine task-progress bar chart."""
    from pptx import Presentation
    from pptx.util import Inches,Pt
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor
    prs=Presentation()
    prs.slide_width=Inches(13.333)
    prs.slide_height=Inches(7.5)
    bg=RGBColor(18,27,42)
    white=RGBColor(250,250,252)
    muted=RGBColor(174,186,201)
    teal=RGBColor(75,207,178)
    panel=RGBColor(37,51,73)
    purple=RGBColor(107,135,221)
    def rect(sl,x,y,w,h,c):
        shape=sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x),
                                  Inches(y),Inches(w),Inches(h))
        shape.fill.solid();shape.fill.fore_color.rgb=c;shape.line.fill.background()
    def txt(sl,t,x,y,w,h,pt=18,color=white,bold=False):
        box=sl.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
        tf=box.text_frame;tf.clear();tf.word_wrap=True
        p=tf.paragraphs[0];run=p.add_run();run.text=str(t)
        run.font.name="Aptos";run.font.size=Pt(pt)
        run.font.bold=bold;run.font.color.rgb=color
        return box
    total=len(deck["slides"])
    for index,slide in enumerate(deck["slides"],1):
        sl=prs.slides.add_slide(prs.slide_layouts[6])
        sl.background.fill.solid();sl.background.fill.fore_color.rgb=bg
        rect(sl,.3,.25,.14,6.8,teal)
        txt(sl,"OBSCURA  /  MEITA",.68,.30,7,.35,12,teal,True)
        txt(sl,str(index)+" / "+str(total),11.8,.3,1,.35,11,muted)
        txt(sl,"AI-GENERATED · INTERNAL DRAFT · VERIFY FACTS BEFORE EXTERNAL USE",
            .68,7.05,11.6,.26,9,muted)
        if slide["kind"]=="cover":
            rect(sl,8.7,1.7,3.1,3.1,panel)
            rect(sl,9.1,2.1,2.3,2.3,purple)
            rect(sl,9.5,2.5,1.5,1.5,teal)
            txt(sl,"MEITA",.75,1.5,7,.7,25,teal,True)
            txt(sl,slide["title"][:95],.75,2.65,7.5,2.9,38,white,True)
            txt(sl,slide["subtitle"],.76,6.0,10,.5,16,muted)
            continue
        txt(sl,slide["title"][:125],.76,.94,11.7,.95,30,white,True)
        txt(sl,slide.get("subtitle","")[:150],.76,1.93,11.6,.5,14,muted)
        if slide["kind"]=="overview":
            for i,item in enumerate(slide["metrics"]):
                x=.73+i*3.15
                rect(sl,x,2.65,2.9,1.28,panel)
                txt(sl,item["value"],x+.17,2.75,2.5,.7,33,teal,True)
                txt(sl,item["label"],x+.17,3.52,2.5,.35,12,white)
            start=4.33;points=slide.get("bullets",[])[:3]
        elif slide["kind"]=="chart":
            bars=slide.get("bars",[])[:9]
            for i,b in enumerate(bars):
                y=2.62+i*.44
                txt(sl,b["name"][:43],.75,y,3.7,.34,12,white,True)
                rect(sl,4.5,y+.06,6.6,.19,panel)
                fraction=b["delivered"]/b["total"] if b["total"] else 0
                if fraction:rect(sl,4.5,y+.06,max(.06,6.6*fraction),.19,teal)
                txt(sl,str(b["delivered"])+"/"+str(b["total"]),11.4,y,.9,.3,12,muted)
            continue
        else:
            points=slide.get("bullets",[])[:4]
            start=2.8
            if slide["kind"]=="work":
                rect(sl,.74,2.5,11.8,.6,panel)
                txt(sl,slide["owner"]+"  ·  "+slide["status"],.93,2.64,11.1,.35,
                    13,teal,True)
                start=3.3
        for i,point in enumerate(points):
            y=start+i*.77
            rect(sl,.76,y+.13,.1,.1,teal)
            txt(sl,point,.96,y,11.4,.62,16,white)
        if slide.get("note"):
            txt(sl,slide["note"],.87,6.52,11.4,.35,10,muted)
    buffer=io.BytesIO();prs.save(buffer);buffer.seek(0)
    return buffer
