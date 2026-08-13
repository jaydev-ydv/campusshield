import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import type { CampusLocation } from '../lib/api'
import { LocationMap } from './LocationMap'

/**
 * Synthetic coordinates for a component test only — never seeded anywhere,
 * never presented as a real campus location. See CAMPUS_LOCATIONS.md: zero
 * coordinates are verified for the real campus, and none are invented here
 * either — this is a fixture, not a claim about a real place.
 */
const MAPPED: CampusLocation = {
  location_id: 1,
  code: 'TEST-LIBRARY',
  name: 'Test Library',
  location_type: 'library',
  zone: null,
  latitude: 12.5,
  longitude: 77.5,
  is_indoor: true,
  dispatch_note: null,
  is_synthetic: false,
}

const MAPPED_SECOND: CampusLocation = {
  ...MAPPED,
  location_id: 2,
  code: 'TEST-CAFE',
  name: 'Test Cafeteria',
  latitude: 12.501,
  longitude: 77.501,
}

const DEMO: CampusLocation = {
  ...MAPPED,
  location_id: 4,
  code: 'DEMO-TEST',
  name: 'DEMO — Test Location (NOT A REAL LOCATION)',
  latitude: 0.001,
  longitude: 0.001,
  is_synthetic: true,
}

const UNMAPPED: CampusLocation = {
  ...MAPPED,
  location_id: 3,
  code: 'TEST-UNSURVEYED',
  name: 'Test Unsurveyed Spot',
  latitude: null,
  longitude: null,
}

describe('LocationMap', () => {
  it('renders nothing when no location carries a coordinate', () => {
    const { container } = render(
      <LocationMap locations={[UNMAPPED]} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the map when a location has a verified coordinate', async () => {
    render(<LocationMap locations={[MAPPED]} selectedId={null} onSelect={vi.fn()} />)
    expect(
      await screen.findByRole('application', { name: /campus location map/i }),
    ).toBeInTheDocument()
  })

  it('ignores an unmapped location alongside mapped ones, without failing', async () => {
    render(<LocationMap locations={[MAPPED, UNMAPPED]} selectedId={null} onSelect={vi.fn()} />)
    expect(await screen.findByTestId('location-map')).toBeInTheDocument()
  })

  it('calls onSelect when a marker is activated', async () => {
    const onSelect = vi.fn()
    render(
      <LocationMap
        locations={[MAPPED, MAPPED_SECOND]}
        selectedId={null}
        onSelect={onSelect}
      />,
    )

    const map = await screen.findByTestId('location-map')
    const marker = map.querySelector('path.leaflet-interactive')
    expect(marker).not.toBeNull()

    marker!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))

    expect(onSelect).toHaveBeenCalledWith(expect.any(Number))
    expect([MAPPED.location_id, MAPPED_SECOND.location_id]).toContain(
      onSelect.mock.calls[0][0],
    )
  })

  it('cleans up the map instance on unmount without throwing', () => {
    const { unmount } = render(
      <LocationMap locations={[MAPPED]} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(() => unmount()).not.toThrow()
  })

  it('shows no demo-data notice when every location is real', async () => {
    render(<LocationMap locations={[MAPPED]} selectedId={null} onSelect={vi.fn()} />)
    await screen.findByTestId('location-map')
    expect(screen.queryByTestId('demo-data-notice')).not.toBeInTheDocument()
  })

  it('shows a demo-data notice when a synthetic location is present', async () => {
    render(<LocationMap locations={[MAPPED, DEMO]} selectedId={null} onSelect={vi.fn()} />)
    expect(await screen.findByTestId('demo-data-notice')).toHaveTextContent(
      /demo campus locations/i,
    )
  })
})
