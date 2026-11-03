// Loads the ad-zone SDK if it is available. The doctor app never talks to the ad
// zone directly; it only tells the SDK where a permitted slot is on screen.
// If the SDK is missing or fails, the doctor app simply shows no sponsored units.

let sdk = null;
let loading = null;
const handles = new Set();

async function load() {
  if (sdk) return sdk;
  if (!loading) {
    loading = import("/ads/slot.js").then((m) => (sdk = m)).catch((e) => { console.warn("ad SDK unavailable", e); return null; });
  }
  return loading;
}

export const ads = {
  // slot: "schedule_card" | "drafting_wait" | "post_sign"
  async mount(container, slot, me) {
    const m = await load();
    if (!m || !container || !container.isConnected) return null;
    try {
      const p = me.prescriber;
      const handle = await m.mountSlot(container, { slot, prescriberId: p.id, specialty: p.specialty, state: p.state });
      if (handle) handles.add(handle);
      return handle;
    } catch (e) { console.warn("ad slot failed", e); return null; }
  },
  async openTray(me) {
    const m = await load();
    if (!m || !m.openResourcesTray) return;
    const p = me.prescriber;
    m.openResourcesTray({ prescriberId: p.id, specialty: p.specialty, state: p.state });
  },
  unmountAll() {
    for (const h of handles) { try { h.unmount(); } catch { /* ignore */ } }
    handles.clear();
    if (sdk && sdk.unmountAllSlots) { try { sdk.unmountAllSlots(); } catch { /* ignore */ } }
  },
};
