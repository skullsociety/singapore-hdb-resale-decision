const status = document.getElementById('status');

async function refresh() {
  const {collectorState} = await chrome.storage.local.get('collectorState');
  let health;
  try { health = await chrome.runtime.sendMessage({type: 'HEALTH'}); }
  catch { health = {error: 'Receiver unavailable'}; }
  const progress = collectorState?.status || 'Ready to start';
  status.textContent = health?.status === 'ready' ? progress :
    `Receiver offline. Start 15_run_propertyguru_browser.ps1, then start a new run.\n${progress}`;
}

document.getElementById('start').addEventListener('click', async () => {
  try {
    const health = await chrome.runtime.sendMessage({type: 'HEALTH'});
    if (health?.status !== 'ready') throw new Error('Start 15_run_propertyguru_browser.ps1 first');
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const site = findListingSite(tab?.url || '');
    if (!site) throw new Error('Open a supported property results tab first');
    const runId = crypto.randomUUID();
    await chrome.storage.local.set({collectorState: {
      active: true, runId, sourceSite: site.id, tabId: tab.id, savedPages: [],
      status: `Starting ${site.name} at page 1`, pageStartedAt: null
    }});
    if (tab.url === site.startUrl) {
      // A tab that was open when the extension was installed has no content script yet.
      await chrome.tabs.reload(tab.id);
    } else {
      await chrome.tabs.update(tab.id, {url: site.startUrl});
    }
    await refresh();
  } catch (error) {
    status.textContent = String(error);
  }
});

document.getElementById('stop').addEventListener('click', async () => {
  const {collectorState} = await chrome.storage.local.get('collectorState');
  if (collectorState) {
    await chrome.storage.local.set({collectorState: {...collectorState, active: false, status: 'Stopped by user'}});
    try { await chrome.tabs.sendMessage(collectorState.tabId, {type: 'STOP'}); } catch {}
  }
  await refresh();
});

chrome.storage.onChanged.addListener(refresh);
setInterval(refresh, 5000);
refresh();
