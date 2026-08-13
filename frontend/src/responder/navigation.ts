/**
 * Turning a campus location into a navigation action.
 *
 * ## Why this is an interface and not three lines of string concatenation
 *
 * A university deploying this will have an opinion about which map application
 * its security team uses, and that opinion is not ours to hard-code. Some run
 * Google Maps, some run OpenStreetMap, and a campus with its own indoor
 * wayfinding system has something better than either. `NavigationProvider` is
 * the seam where that choice is made once.
 *
 * ## What is deliberately not here
 *
 * No routing, no ETA, no turn-by-turn. Handing off to a map application the
 * responder already knows is both more useful and more honest than a route
 * drawn over a campus this system has never surveyed.
 *
 * ## The destination is always the selected campus location
 *
 * Never a photograph's GPS, never the reporter's device position. Those are
 * corroborating signals; navigating to one would send a responder to wherever a
 * picture happened to be taken.
 */
import type { Destination } from '../lib/api'

export interface NavigationTarget {
  /** A URL a responder's device can open, or null when there is no coordinate. */
  url: string | null
  /** Always present: what to say when there is nothing to hand to a map. */
  label: string
  provider: string
}

export interface NavigationProvider {
  readonly name: string
  target(destination: Destination): NavigationTarget
}

/**
 * The text a responder needs when the location has no surveyed coordinate.
 *
 * This is the state of every location today, so it is not an edge case — it is
 * the primary path. Name, then zone, then access instructions, then the
 * reporter's own words about where exactly. A responder who reads these can
 * find the place; a campus centroid dropped on a map would only look like they
 * could.
 */
export function destinationDirections(destination: Destination): string[] {
  const lines: string[] = [destination.name]
  if (destination.zone_name) lines.push(destination.zone_name)
  if (destination.is_indoor) lines.push('Indoor location')
  if (destination.dispatch_note) lines.push(destination.dispatch_note)
  if (destination.location_hint) lines.push(`Reporter noted: "${destination.location_hint}"`)
  return lines
}

/**
 * Hands the coordinate to whichever map application the device prefers.
 *
 * `geo:` is the platform-neutral choice — Android offers the user their
 * installed map apps, and it degrades to a no-op rather than forcing a vendor.
 * The `q=` parameter carries the location name so the destination pin is
 * labelled with the building rather than a bare number.
 */
export class GeoUriNavigationProvider implements NavigationProvider {
  readonly name = 'geo'

  target(destination: Destination): NavigationTarget {
    if (
      !destination.navigable ||
      destination.latitude === null ||
      destination.longitude === null
    ) {
      return {
        url: null,
        label: 'No mapped coordinate for this location',
        provider: this.name,
      }
    }
    const { latitude, longitude, name } = destination
    return {
      url: `geo:${latitude},${longitude}?q=${latitude},${longitude}(${encodeURIComponent(name)})`,
      label: `Navigate to ${name}`,
      provider: this.name,
    }
  }
}

/**
 * OpenStreetMap in a browser tab.
 *
 * The fallback for a desktop control room, where a `geo:` URI has nothing to
 * open. OSM rather than a commercial provider because the rest of the stack
 * already uses it, and because it does not report the responder's query to an
 * advertising business.
 */
export class OpenStreetMapNavigationProvider implements NavigationProvider {
  readonly name = 'openstreetmap'

  target(destination: Destination): NavigationTarget {
    if (
      !destination.navigable ||
      destination.latitude === null ||
      destination.longitude === null
    ) {
      return {
        url: null,
        label: 'No mapped coordinate for this location',
        provider: this.name,
      }
    }
    const { latitude, longitude } = destination
    return {
      url: `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=19/${latitude}/${longitude}`,
      label: `Navigate to ${destination.name}`,
      provider: this.name,
    }
  }
}

/**
 * Pick a provider for the device at hand.
 *
 * A phone in someone's hand on campus gets `geo:` and its own map apps; a
 * desktop in a control room gets a browser tab. Deliberately a coarse check —
 * a wrong guess costs a responder one tap, and a user-agent database would be
 * worse than the problem.
 */
export function defaultNavigationProvider(
  userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent,
): NavigationProvider {
  const mobile = /Android|iPhone|iPad|iPod/i.test(userAgent)
  return mobile ? new GeoUriNavigationProvider() : new OpenStreetMapNavigationProvider()
}
