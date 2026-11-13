// Full UI walkthrough of the RescueRoute demo.
// node <skill>/browser.mjs http://127.0.0.1:5000/ --script scripts/qa_demo.mjs --screenshot out.png
export default async function run(page, ui) {
  const out = { steps: [] };
  const wait = (ms) => page.waitForTimeout(ms);

  await page.waitForTimeout(3000);

  // 1. Emergency: first preset (Connaught Place)
  const snap = await ui.snapshot();
  const preset = snap.match(/@(e\d+) button "Connaught Place[^"]*"/)?.[1];
  if (!preset) return { error: "preset button not found", snap };
  await ui.click(preset);
  await wait(1500);
  out.steps.push({ emergency: await page.evaluate(() => document.querySelector("#emergency-coord").innerText) });

  // 2. Place an ambulance at map centre
  const snap2 = await ui.snapshot();
  const addAmb = snap2.match(/@(e\d+) button "＋ Place ambulance[^"]*"/)?.[1];
  await ui.click(addAmb);
  await wait(1200);
  out.steps.push({
    ambulance: await page.evaluate(() => document.querySelector("#ambulance-select").innerText),
    hospitalOptions: await page.evaluate(() =>
      [...document.querySelectorAll("#hospital-select option")].map((o) => o.textContent).slice(0, 6)),
  });

  // 3. Pick the first real hospital
  const hospValue = await page.evaluate(() => {
    const sel = document.querySelector("#hospital-select");
    return sel.options[1]?.value || "";
  });
  if (!hospValue) return { error: "no hospitals loaded", out };
  await page.selectOption("#hospital-select", hospValue);
  await wait(600);
  out.steps.push({ hospitalCard: await page.evaluate(() => document.querySelector("#hospital-card").innerText) });

  // 4. Dispatch
  const snap3 = await ui.snapshot();
  const plan = snap3.match(/@(e\d+) button "Plan & dispatch mission"/)?.[1];
  await ui.click(plan);
  await wait(6000);

  out.steps.push({
    mission: await page.evaluate(() => ({
      status: document.querySelector("#m-status").innerText,
      eta: document.querySelector("#m-eta").innerText,
      remaining: document.querySelector("#m-remaining").innerText,
      travelled: document.querySelector("#m-travelled").innerText,
      reroutes: document.querySelector("#m-reroutes").innerText,
    })),
    mapPolylines: await page.evaluate(() => document.querySelectorAll("#map svg path").length),
    markers: await page.evaluate(() => document.querySelectorAll(".amb-icon, .emg-pin, .hosp-pin").length),
    bench: await page.evaluate(() => document.querySelector("#bench-table").innerText.replace(/\n/g, " | ")),
    benchNote: await page.evaluate(() => document.querySelector("#bench-note").innerText),
  });

  return out;
}
