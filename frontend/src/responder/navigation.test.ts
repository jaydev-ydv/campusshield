import { describe, expect, it } from 'vitest'

import type { Destination } from '../lib/api'
import {
  GeoUriNavigationProvider,
  OpenStreetMapNavigationProvider,
  defaultNavigationProvider,
  destinationDirections,
} from './navigation'

function destination(overrides: Partial<Destination> = {}): Destination {
  return {
    location_id: 1,
    code: 'LKRC-MAIN',
    name: 'Library & Knowledge Resource Centre',
    // Synthetic, and only ever compared against itself. No Presidency
    // University coordinate is asserted anywhere in this suite.
    latitude: 12.5,
    longitude: 77.5,
    is_mapped: true,
    navigable: true,
    location_type: 'library',
    is_indoor: true,
    dispatch_note: 'Enter via the service gate; lift to level 2.',
    zone_name: 'North Zone',
    location_hint: null,
    is_synthetic: false,
    ...overrides,
  }
}

const unmapped = destination({
  latitude: null,
  longitude: null,
  is_mapped: false,
  navigable: false,
})

describe('navigation providers', () => {
  it('builds a geo: URI carrying the location name', () => {
    const target = new GeoUriNavigationProvider().target(destination())
    expect(target.url).toContain('geo:12.5,77.5')
    expect(target.url).toContain(encodeURIComponent('Library & Knowledge Resource Centre'))
  })

  it('builds an OpenStreetMap URL', () => {
    const target = new OpenStreetMapNavigationProvider().target(destination())
    expect(target.url).toContain('openstreetmap.org')
    expect(target.url).toContain('mlat=12.5')
  })

  it('returns no URL when the location has no verified coordinate', () => {
    // The state of every campus location today. A provider that invented a
    // centroid here would send a responder to the wrong building.
    for (const provider of [
      new GeoUriNavigationProvider(),
      new OpenStreetMapNavigationProvider(),
    ]) {
      const target = provider.target(unmapped)
      expect(target.url).toBeNull()
      expect(target.label).toMatch(/no mapped coordinate/i)
    }
  })

  it('refuses to navigate when navigable is false even if coordinates are present', () => {
    // `navigable` is false for a provisional coordinate — a point nobody has
    // stood at. The provider trusts that flag over the raw numbers.
    const provisional = destination({ navigable: false })
    expect(new GeoUriNavigationProvider().target(provisional).url).toBeNull()
  })

  it('picks geo: on a phone and a browser map on a desktop', () => {
    expect(defaultNavigationProvider('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)').name).toBe(
      'geo',
    )
    expect(
      defaultNavigationProvider('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)').name,
    ).toBe('openstreetmap')
  })
})

describe('destinationDirections', () => {
  it('leads with the location name', () => {
    expect(destinationDirections(destination())[0]).toBe('Library & Knowledge Resource Centre')
  })

  it('carries the access instructions that make the last hundred metres work', () => {
    const lines = destinationDirections(destination())
    expect(lines).toContain('Enter via the service gate; lift to level 2.')
    expect(lines).toContain('North Zone')
    expect(lines).toContain('Indoor location')
  })

  it("quotes the reporter's own words about where exactly", () => {
    const lines = destinationDirections(
      destination({ location_hint: 'near the rear stairwell' }),
    )
    expect(lines.some((line) => line.includes('near the rear stairwell'))).toBe(true)
  })

  it('still gives usable directions for an unmapped location', () => {
    // The primary path today, not an edge case.
    const lines = destinationDirections(unmapped)
    expect(lines[0]).toBe('Library & Knowledge Resource Centre')
    expect(lines).toContain('Enter via the service gate; lift to level 2.')
  })
})
