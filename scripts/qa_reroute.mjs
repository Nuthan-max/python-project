// Signature demo in the UI: dispatch, block a road ahead, watch the reroute.
// node <skill>/browser.mjs http://127.0.0.1:5000/ --script scripts/qa_reroute.mjs --screenshot out.png
export default async function run(page, ui) {
  const wait = (ms) => page.waitForTimeout(ms);
  const out = { steps: [] };
  await wait(3000);

  // Dispatch (preset emergency -> ambulance -> hospital -> plan)
  let snap = await ui.snapshot();
  await ui.click(snap.match(/@(e\d+) button "India Gate[^"]*"/)[1]);
  await wait(1500);
  snap = await ui.snapshot();
  await ui.click(snap.match(/@(e\d+) button "＋ Place ambulance[^"]*"/)[1]);
  await wait(1500);
  const hosp = await page.evaluate(() => document.querySelector("#hospital-select").options[1]?.value);
  await page.selectOption("#hospital-select", hosp);
  await wait(500);
  snap = await ui.snapshot();
  await ui.click(snap.match(/@(e\d+) button "Plan & dispatch mission"/)[1]);
  await wait(7000);

  out.afterDispatch = await page.evaluate(() => ({
    status: document.querySelector("#m-status").innerText,
    travelled: document.querySelector("#m-travelled").innerText,
    reroutes: document.querySelector("#m-reroutes").innerText,
  }));
  out.missionId = await page.evaluate(async () => {
    const r = await fetch("/api/missions");
    const j = await r.json();
    return j.items.map((m) => m.id);
  });

  // Close the street ~150 m ahead of the vehicle via the real API,
  // exactly what the "Block road" map mode does.
  out.incident = await page.evaluate(async () => {
    const list = await (await fetch("/api/missions")).json();
    const m = list.items[list.items.length - 1];
    const steps = m.legs[m.leg_index].steps;
    let acc = 0, target = null;
    for (const s of steps) {
      acc += s.distance_m;
      if (acc > m.distance_travelled_m + 150) { target = s; break; }
    }
    target = target || steps[steps.length - 1];
    const res = await fetch("/api/incidents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        u: target.from[0], v: target.to[0], kind: "closure", level: "closed",
        label: "Demo closure (UI)", lat: target.from[1], lng: target.from[2],
      }),
    });
    return { street: target.street, distanceAhead: Math.round(acc - m.distance_travelled_m), ...(await res.json()) };
  });

  await wait(6000);
  out.afterReroute = await page.evaluate(() => ({
    status: document.querySelector("#m-status").innerText,
    reroutes: document.querySelector("#m-reroutes").innerText,
    events: [...document.querySelectorAll("#m-events li")].map((li) => li.innerText),
  }));

  // Switch to the incident tab so the screenshot shows the disruption list
  snap = await ui.snapshot();
  const trafficTab = snap.match(/@(e\d+) button "Traffic & Incidents"/)[1];
  await ui.click(trafficTab);
  await wait(800);
  out.incidentTab = await page.evaluate(() => document.querySelector("#incident-list").innerText);
  return out;
}
