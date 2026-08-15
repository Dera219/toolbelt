/**
 * Coordinate resolution.
 *
 * The API matches workers to jobs with a bounding box plus haversine
 * (api/app/modules/jobs/service.py), so a lat/lng is mandatory — a job posted
 * without one cannot be found by anybody. GPS is the best source and is still
 * tried first every time, but a denied permission used to be a dead end: the
 * screens threw and the user had no way forward. That is invisible on a phone,
 * where the OS prompt is one tap, and a wall on the web build, where a denied
 * site permission is buried in browser settings.
 *
 * So: GPS first, then geocode an address the user already typed, then a
 * location they saved earlier. Only when all three are unavailable do we fail,
 * and the message names what the user can actually do about it.
 */

import * as Location from "expo-location";
import { Platform } from "react-native";

import { ApiError } from "./api/client";

export interface Coords {
  lat: number;
  lng: number;
}

/** Where a resolved coordinate came from, so callers can be honest about it. */
export type CoordsSource = "gps" | "address" | "saved";

export interface ResolvedCoords extends Coords {
  source: CoordsSource;
}

/**
 * Photon (photon.komoot.io) — OpenStreetMap data, free, no API key, and it
 * answers with `Access-Control-Allow-Origin: *`, which is what the web build
 * needs.
 *
 * Nominatim was the obvious first choice and was rejected on evidence: it sits
 * behind a shared Varnish cache whose `vary` does not include `Origin`, so a
 * response cached from a non-browser request is replayed to browsers *without*
 * the CORS header and the fetch is blocked. That is reproducible, and it fails
 * intermittently rather than cleanly. Browsers also forbid setting
 * `User-Agent`, so Nominatim's "identify yourself" requirement cannot be
 * honoured from web at all.
 *
 * Photon asks for fair use rather than publishing a hard rate limit. We
 * geocode only on an explicit submit — never per keystroke — and serialise
 * requests at most one per second below, which keeps us well inside both this
 * policy and Nominatim's stricter 1 req/sec rule should we ever switch back.
 */
const GEOCODER_URL = "https://photon.komoot.io/api/";
const GEOCODER_MIN_INTERVAL_MS = 1000;
const GEOCODER_TIMEOUT_MS = 8000;
const GEOCODER_USER_AGENT = "ToolBelt/1.0 (+https://toolbelt.biz)";

/** Shorter than this is a typo, not an address worth spending a request on. */
const MIN_ADDRESS_LENGTH = 3;

interface PhotonResponse {
  features?: { geometry?: { coordinates?: [number, number] } }[];
}

let geocodeQueue: Promise<unknown> = Promise.resolve();
let lastGeocodeAt = 0;

/**
 * Run geocoding requests one at a time, at most one per second, however many
 * screens ask at once. A rejection must not poison the queue for the next
 * caller, hence the swallowed catch on the chain itself.
 */
function throttled<T>(fn: () => Promise<T>): Promise<T> {
  const run = geocodeQueue.then(async () => {
    const wait = GEOCODER_MIN_INTERVAL_MS - (Date.now() - lastGeocodeAt);
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    lastGeocodeAt = Date.now();
    return fn();
  });
  geocodeQueue = run.catch(() => undefined);
  return run;
}

/**
 * Accept a coordinate pair only if the API would also accept it — the job and
 * profile schemas bound lat to ±90 and lng to ±180, so anything else is a 422
 * we can catch here. Exactly (0, 0) is open ocean off Africa; in practice it
 * means an unset field, so it is treated as missing rather than as a location.
 */
function validCoords(lat: unknown, lng: unknown): Coords | null {
  if (typeof lat !== "number" || typeof lng !== "number") return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  if (lat === 0 && lng === 0) return null;
  return { lat, lng };
}

/** Ask the hosted geocoder. Returns null for anything short of a clean hit. */
async function geocodeRemotely(query: string): Promise<Coords | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GEOCODER_TIMEOUT_MS);
  try {
    const resp = await fetch(`${GEOCODER_URL}?limit=1&q=${encodeURIComponent(query)}`, {
      signal: controller.signal,
      // Browsers refuse to set User-Agent, and sending any custom header would
      // turn this into a preflighted request for no gain — so identify the app
      // only on native, where the header actually survives.
      headers: Platform.OS === "web" ? undefined : { "User-Agent": GEOCODER_USER_AGENT },
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as PhotonResponse;
    // GeoJSON orders coordinates [longitude, latitude].
    const [lng, lat] = data.features?.[0]?.geometry?.coordinates ?? [];
    return validCoords(lat, lng);
  } catch {
    // Offline, CORS, timeout, malformed JSON — all the same to the caller.
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Turn a typed address into coordinates.
 *
 * On iOS and Android we use the OS geocoder first: it is more accurate, costs
 * no third-party request, and needs no location permission of its own. Its web
 * implementation throws `E_NO_GEOCODER` unconditionally, and Android builds
 * without Google Play services throw too, so a failure there falls through to
 * the hosted geocoder rather than ending the attempt.
 */
async function geocodeAddress(address: string): Promise<Coords | null> {
  const query = address.trim();
  if (query.length < MIN_ADDRESS_LENGTH) return null;

  if (Platform.OS !== "web") {
    try {
      const [hit] = await Location.geocodeAsync(query);
      const coords = hit ? validCoords(hit.latitude, hit.longitude) : null;
      if (coords) return coords;
    } catch {
      // No OS geocoder on this device — the hosted one below still works.
    }
  }

  return throttled(() => geocodeRemotely(query));
}

/**
 * The original GPS path, unchanged in effect: request permission, and read a
 * fix only once it is granted. The difference is that failure now returns null
 * for the caller to route around instead of throwing. `requestForeground-
 * PermissionsAsync` itself throws on web when `navigator.permissions` is
 * missing (older browsers, or a page served over plain http), which previously
 * surfaced as an unexplained generic error.
 */
async function currentPosition(): Promise<Coords | null> {
  try {
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== "granted") return null;
    const pos = await Location.getCurrentPositionAsync({});
    return validCoords(pos.coords.latitude, pos.coords.longitude);
  } catch {
    return null;
  }
}

/**
 * Resolve a usable coordinate, preferring the most precise source available.
 *
 * @param address  Address the user typed on this screen, if the screen has one.
 * @param saved    A location already stored for this user, e.g. a worker's base.
 * @param unavailableMessage  Shown when nothing worked. Say what the user can
 *   do next — every caller reaches it only after GPS *and* the fallbacks fail.
 */
export async function resolveCoords({
  address,
  saved,
  unavailableMessage,
}: {
  address?: string;
  saved?: Coords | null;
  unavailableMessage: string;
}): Promise<ResolvedCoords> {
  const gps = await currentPosition();
  if (gps) return { ...gps, source: "gps" };

  if (address != null && address.trim().length > 0) {
    const geocoded = await geocodeAddress(address);
    if (geocoded) return { ...geocoded, source: "address" };
  }

  const fallback = saved ? validCoords(saved.lat, saved.lng) : null;
  if (fallback) return { ...fallback, source: "saved" };

  // Status 0 is this codebase's marker for a failure with no HTTP response
  // behind it — see the transport error in api/client.ts and the local
  // validation errors in the screens that call this.
  throw new ApiError(0, unavailableMessage);
}
