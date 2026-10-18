import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

RATES_V1 = """# Rates

Certificates

Special Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 10 Month Fixed Jumbo Certificate Special | 3.86% | 3.90%1 | $100,000 |
| 10 Month Fixed Certificate Special | 3.76% | 3.80%2 | $1,000 |

Rates are effective as of 03/03/2026 and are subject to change without notice.

Auto & Motorcycle Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2023-26 Model Year | 60 Months | 5.34% APR1 |
| 2023-26 Model Year | 72 Months | 5.49% APR1 |
"""
RATES_V2 = RATES_V1.replace("3.86% | 3.90%1", "3.96% | 4.00%1").replace("2023-26 Model Year | 60 Months | 5.34%", "2024-27 Model Year | 60 Months | 5.39%")
CALC = """# Vehicle affordability calculator

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2023-26 Model Year | 60 Months | 5.34% APR |
"""
CONTACT = "# Contact\n\n**Routing Number:** 273974581\n\nCall 800-373-7600.\n"


def page(url, md, when):
    return Page(url=url, title=url.rsplit("/", 1)[-1] + " | Dupaco", markdown=md, fetched_at=when + "T00:00:00Z")


def snapshots():
    s1 = Snapshot("01_2026-05-12_baseline", "2026-05-12", [
        page("https://www.dupaco.com/rates", RATES_V1, "2026-05-12"),
        page("https://www.dupaco.com/calculators/vehicle-affordability", CALC, "2026-05-12"),
        page("https://www.dupaco.com/contact", CONTACT, "2026-05-12"),
    ])
    s2 = Snapshot("02_2026-09-28_latest", "2026-09-28", [
        page("https://www.dupaco.com/rates", RATES_V2, "2026-09-28"),
        page("https://www.dupaco.com/calculators/vehicle-affordability", CALC, "2026-09-28"),
        page("https://www.dupaco.com/contact", CONTACT, "2026-09-28"),
    ])
    return [s1, s2]
