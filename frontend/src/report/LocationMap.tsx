import { useEffect, useRef } from 'react'
import L from 'leaflet'

import type { CampusLocation } from '../lib/api'

/**
 * The campus map, for choosing where something happened.
 *
 * ## The list below this map is not a fallback — it is the interface
 *
 * `GET /locations` returns only locations whose coordinates have been
 * verified (`core.campus_location.is_active` cannot be true otherwise), so
 * every location this component is given can, in principle, be drawn. But
 * the `<Select>` next to it in `StepLocation` is the thing a screen reader
 * uses, the thing that works on the slowest phone, and the thing that keeps
 * working if a tile server is unreachable. This map is a second way to reach
 * the same choice, not a replacement for the first — a student can complete
 * the whole form without ever touching it.
 *
 * ## Leaflet directly, matching `responder/IncidentMap.tsx`
 *
 * Same library, same imperative approach, for the same reason: about thirty
 * lines against Leaflet's own API beats a React wrapper that has to track
 * Leaflet's release cycle. The two maps intentionally do not share a
 * component — one is a read-heavy, multi-marker responder view with its own
 * selection semantics; this one is a single-choice picker a student taps
 * once. Sharing code between them would couple two things that change for
 * different reasons.
 *
 * ## No coordinate is ever invented
 *
 * A location with no verified coordinate is never handed to this component
 * in the first place — `StepLocation` shows its own empty state when
 * `locations` is empty, and this component additionally filters defensively
 * in case that invariant is ever violated upstream. There is no synthetic
 * pin, no campus-centroid placeholder, nothing standing in for a place
 * nobody has surveyed.
 *
 * ## Demo locations are marked, never disguised (Phase 5B)
 *
 * A location can be `coordinate_status = 'verified'` *and*
 * `is_synthetic = true` — a development/demo fixture that behaves exactly
 * like a real one so the map genuinely works before a field survey exists,
 * but must never be presented as if it were one. A demo marker renders with
 * a visibly different style and an appended "(DEMO)" in its tooltip; if any
 * location shown is synthetic, a banner says so above the map as well.
 */
export function LocationMap({
  locations,
  selectedId,
  onSelect,
}: {
  locations: CampusLocation[]
  selectedId: number | null
  onSelect: (locationId: number) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const markerLayer = useRef<L.LayerGroup | null>(null)

  const mappable = locations.filter(
    (location) => location.latitude !== null && location.longitude !== null,
  )

  useEffect(() => {
    if (mappable.length === 0 || !container.current || map.current) return

    const instance = L.map(container.current, {
      zoomControl: true,
      scrollWheelZoom: false,
    })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors',
    }).addTo(instance)

    markerLayer.current = L.layerGroup().addTo(instance)
    map.current = instance

    return () => {
      instance.remove()
      map.current = null
      markerLayer.current = null
    }
  }, [mappable.length])

  useEffect(() => {
    const instance = map.current
    const layer = markerLayer.current
    if (!instance || !layer) return

    layer.clearLayers()
    const points: L.LatLngExpression[] = []

    for (const location of mappable) {
      const point: L.LatLngExpression = [
        location.latitude as number,
        location.longitude as number,
      ]
      points.push(point)

      const selected = location.location_id === selectedId
      const demo = location.is_synthetic

      const marker = L.circleMarker(point, {
        radius: selected ? 11 : 8,
        weight: selected ? 4 : 2,
        // Demo markers use a visibly different (dashed, amber) style so a
        // developer or evaluator can never mistake one for a surveyed pin
        // by appearance alone — the tooltip and the banner above the map
        // say so explicitly too.
        dashArray: demo ? '4 3' : undefined,
        color: selected ? '#1d4ed8' : demo ? '#b45309' : '#475569',
        fillColor: selected ? '#3b82f6' : demo ? '#f59e0b' : '#94a3b8',
        fillOpacity: 0.85,
      })

      marker.bindTooltip(demo ? `${location.name} (DEMO)` : location.name, {
        direction: 'top',
      })
      marker.on('click', () => onSelect(location.location_id))
      // Reachable without a mouse — matches responder/IncidentMap.tsx.
      marker.on('keypress', () => onSelect(location.location_id))
      layer.addLayer(marker)
    }

    if (points.length > 0) {
      instance.fitBounds(L.latLngBounds(points).pad(0.25), { maxZoom: 18 })
    }
  }, [mappable, selectedId, onSelect])

  // Nothing mappable: `StepLocation` already renders its own empty state
  // above this point for an empty `locations` list, so this only fires if
  // a non-empty list somehow carries no coordinates — an invariant this
  // component does not trust blindly. Rendering nothing here is correct
  // either way: the `<Select>` below remains fully usable on its own.
  if (mappable.length === 0) {
    return null
  }

  const hasDemoLocations = mappable.some((location) => location.is_synthetic)

  return (
    <div className="space-y-2">
      {hasDemoLocations && (
        <p
          role="status"
          data-testid="demo-data-notice"
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900"
        >
          Demo campus locations — for development and testing only. Not verified Presidency
          University data.
        </p>
      )}
      <div
        ref={container}
        role="application"
        aria-label="Campus location map — select a marker, or use the list below"
        data-testid="location-map"
        className="border-ink-200 h-64 w-full overflow-hidden rounded-xl border sm:h-80"
      />
    </div>
  )
}
