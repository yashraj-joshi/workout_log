// The shared vocabulary: groups, muscles, the name lookup and common names.
// The twin of backend/src/workoutlog/catalog.py, reading the same file.
//
// `make sync-shared` copies shared/exercise_catalog.json to web/, and a test
// fails if the copies drift. Nothing here is user data, so the service worker
// caches it with the rest of the shell.

let data = null;

// Called once at boot, before anything renders: lookup() is synchronous
// everywhere else, exactly as it is in Python.
export async function loadCatalog(fetchImpl = (...args) => fetch(...args)) {
  if (!data) {
    const res = await fetchImpl("/exercise_catalog.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`exercise_catalog.json: ${res.status}`);
    setCatalog(await res.json());
  }
  return data;
}

// For the tests, which read the file off disk.
export function setCatalog(json) {
  data = json;
  index = null;
}

export const loaded = () => data !== null;
export const groups = () => data.groups;
export const muscles = () => data.muscles;
export const lookupRules = () => data.lookup;
export const commonNames = () => data.commonNames;
export const mainAreas = () => data.mainAreas;

let index = null;

// Lowercase muscle name -> canonical spelling, so typed input is forgiving.
export function canonicalMuscle(name) {
  if (!index) index = new Map(muscles().map((m) => [m.toLowerCase(), m]));
  return index.get(String(name || "").trim().toLowerCase()) || null;
}
