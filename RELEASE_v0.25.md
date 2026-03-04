# Shrync v0.25 — Multi-encoder, HDR bewaring, GPU Monitor, CA Template

## Nieuwe functies

### Meerdere encoder profielen — Nvidia, AMD en Intel

Naast de bestaande Nvidia NVENC profielen ondersteunt Shrync nu ook AMD AMF en Intel QSV encoders. De container detecteert automatisch welk GPU type aanwezig is.

**Nvidia NVENC** (GTX 900+)
- `nvenc_max` — Max kwaliteit (p7 / QP 19)
- `nvenc_high` — Hoge kwaliteit (p5 / QP 22)
- `nvenc_balanced` — Gebalanceerd (p4 / QP 26)
- `h264_nvenc` — H.264 GPU (p5 / QP 20)

**AMD AMF** (RX 400+) — vereist `--device /dev/dri` in Extra Parameters
- `amf_max` — Max kwaliteit
- `amf_balanced` — Gebalanceerd
- `h264_amf` — H.264 AMD GPU

**Intel QSV** (Gen 6+) — vereist `--device /dev/dri` in Extra Parameters
- `qsv_max` — Max kwaliteit
- `qsv_balanced` — Gebalanceerd
- `h264_qsv` — H.264 Intel GPU

**CPU** (ongewijzigd)
- `cpu_slow`, `cpu_medium`, `cpu_fast`, `h264_cpu`

In de instellingenpagina worden de profielen per encoder gegroepeerd weergegeven. Niet-beschikbare encoders worden grijs en uitgeschakeld getoond.

### HDR / Dolby Vision metadata bewaring

ffprobe detecteert automatisch het HDR type van elk bronbestand:
- **HDR10** — kleurruimte BT.2020 + PQ transfer
- **HDR10+** — dynamische HDR metadata
- **HLG** — Hybrid Log-Gamma
- **Dolby Vision** — RPU metadata via side_data

Bij HDR-inhoud worden de correcte kleurruimte flags meegegeven aan ffmpeg en wordt automatisch 10-bit pixel format (`p010le`) gekozen zodat de kwaliteit behouden blijft.

### Live GPU Monitor widget op het dashboard

Op het dashboard verschijnt automatisch een GPU Monitor widget als er een GPU actief is. Wordt elke 5 seconden ververst.

**Nvidia:** GPU gebruik %, VRAM gebruik (GB), temperatuur (°C), actieve encoder sessies
**AMD / Intel:** GPU gebruik % via `/sys/class/drm`

### Unraid Community Applications XML template

- Geen persoonlijke paden meer ingevuld — alle mediamappen starten leeg
- `ExtraParams` blijft leeg — gebruikers vullen zelf `--runtime=nvidia` of `--device /dev/dri` in
- GPU modus variabele duidelijk gedocumenteerd
- Beschrijving bijgewerkt met AMD/Intel instructies

---

## Upgraden

```bash
docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.25 .
docker push timmex91/shrync:latest
docker push timmex91/shrync:0.25
```

**AMD GPU extra parameters:** `--device /dev/dri`
**Intel GPU extra parameters:** `--device /dev/dri`
**Nvidia GPU extra parameters:** `--runtime=nvidia`

---

## Versiegeschiedenis

| Tag | Omschrijving |
|---|---|
| `0.25` | Multi-encoder (AMD/Intel), HDR bewaring, GPU Monitor, CA Template |
| `0.20` | Mobiele interface verbeterd |
| `0.19` | Fix: NVENC universele compatibiliteit GTX 900+ |
| `0.18` | Fix: NVENC compressie |
| `0.17` | Fix: dispatcher race condition, watcher detectie |
| `0.16` | Dynamische workers, cache verwijderd, statistieken resetten |
