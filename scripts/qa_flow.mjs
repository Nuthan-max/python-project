export default async function run(page, ui) {
  const result = { logs: [] };

  page.on("console", msg => console.log("[BROWSER CONSOLE]", msg.type(), msg.text()));

  await page.waitForTimeout(2000);

  // 1. Click Connaught Place
  console.log("1. Setting emergency location...");
  await page.locator("#emergency-presets button").first().click();
  await page.waitForTimeout(1000);

  // 2. Select Hospital
  console.log("2. Selecting destination hospital...");
  const hospSelect = page.locator("#hospital-select");
  await page.waitForFunction(() => document.querySelector("#hospital-select").options.length > 1, { timeout: 6000 });
  await hospSelect.selectOption({ index: 1 });
  await page.waitForTimeout(500);

  // 3. Deploy Ambulance
  console.log("3. Deploying ambulance...");
  await page.locator("#btn-add-ambulance").click();
  await page.waitForTimeout(1000);

  // 4. Click Plan & Dispatch
  console.log("4. Dispatching mission...");
  await page.locator("#btn-plan").click();

  // Wait 4 seconds for mission loop
  await page.waitForTimeout(4000);

  result.status = await page.locator("#m-status").innerText();
  result.events = await page.locator("#m-events").innerText();

  console.log("Mission status:", result.status);
  console.log("Mission events:", result.events);

  await page.screenshot({ path: "g:\\python project\\data\\qa-mission-verified.png" });

  return result;
}
