from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
import pandas as pd
from shared.charts import render
from shared.models import ChartSpec
from pathlib import Path

app = FastAPI()


@app.get("/chart-widget.js")
def widget():
    return FileResponse(
        Path(__file__).resolve().parents[1] / "shared/chart-widget.js", media_type="text/javascript"
    )


@app.get("/render")
def chart(region: str = "", hidden: str = "", zoom: str = ""):
    # Explicit synthetic micro-fixture only for validating the interaction technology.
    df = pd.DataFrame(
        {
            "period": ["Янв", "Фев", "Мар", "Апр"],
            "plan": [100.0, 150.0, 120.0, 180.0],
            "fact": [90.0, 155.0, 110.0, 195.0],
        }
    )
    if region:
        df["fact"] *= 0.5
    spec = ChartSpec(
        id="prototype",
        hidden=hidden.split(",") if hidden else [],
        zoom=[float(v) for v in zoom.split(",")] if zoom else None,
    )
    return {"chart": render(df, spec), "spec": spec.model_dump()}


@app.get("/")
def index():
    return HTMLResponse(
        """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Прототип Matplotlib</title><style>body{font-family:system-ui;max-width:900px;margin:30px auto}.chart-tooltip{position:absolute;background:#252539;color:white;padding:10px;pointer-events:none}.chart-tools{display:flex;gap:12px}.chart-svg{touch-action:none}button,select{padding:12px}</style><h1>Технический прототип: Matplotlib SVG</h1><p>Синтетический микротест взаимодействий. Потяните область графика для зума.</p><label>Регион <select id="region"><option value="">Все</option><option value="test">Тестовый</option></select></label><div id="chart"></div><output id="state"></output><script type="module">import{chartWidget}from'/chart-widget.js';let state={};async function update(p={}){state={...state,...p};const q=new URLSearchParams(Object.entries(state).map(([k,v])=>[k,Array.isArray(v)?v.join(','):v||'']));const r=await(await fetch('/render?'+q)).json();document.querySelector('#chart').replaceChildren();chartWidget(document.querySelector('#chart'),r.chart,r.spec,update);document.querySelector('#state').textContent=JSON.stringify(state);}document.querySelector('#region').onchange=e=>update({region:e.target.value});update();</script></html>"""
    )
