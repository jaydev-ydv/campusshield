import { useEffect, useRef } from 'react'
import L from 'leaflet'

import type { IncidentSummary } from '../lib/api'

/**
 * The campus map, with incidents at their controlled locations.
 *
 * ## The empty state is still the real-data state
 *
 * `core.campus_location` holds **zero verified real-world coordinates** —
 * the field survey (`CAMPUS_LOCATIONS.md`) has not happened. Until it does,
 * there is nothing truthful to draw for a real deployment, and this
 * component says so rather than rendering a plausible-looking campus. A map
 * with invented real pins would be worse than no map: a responder would
 * trust it. A development/demo environment can populate the map with
 * clearly-flagged synthetic locations (Phase 5B,
 * `scripts/seed_demo_campus_locations.py`) — see below — but nothing here
 * ever fabricates a *real* coordinate.
 *
 * The incident *list* beside this map is the working interface today, and stays
 * the working interface afterwards — it is what a screen reader can use, what a
 * phone shows well, and what still functions when a location has no coordinate.
 *
 * ## Leaflet directly, not react-leaflet
 *
 * About thirty lines against an imperative API, versus a dependency that would
 * need to track React's major versions. The map is created once and markers are
 * rebuilt when incidents change.
 *
 * ## This is a responder map, not a public one
 *
 * Individual incidents at identifiable points are shown only to staff whose role
 * routes them these reports. The public safety view is a separate, aggregated,
 * k-anonymised thing (`v_public_safety_map`) and is not this.
 *
 * ## Demo incidents are marked, never disguised (Phase 5B)
 *
 * An incident whose location is a demo fixture (`location.is_synthetic`)
 * renders with a dashed marker outline, an appended "(DEMO)" in its
 * tooltip, and triggers a banner above the map — independent of, and
 * layered alongside, the existing emergency/selected colour semantics.
 */
export function IncidentMap({
  incidents,
  selectedRef,
  onSelect,
}: {
  incidents: IncidentSummary[]
  selectedRef: string | null
  onSelect: (publicRef: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const markerLayer = useRef<L.LayerGroup | null>(null)

  const mappable = incidents.filter(
    (incident) => incident.location.latitude !== null && incident.location.longitude !== null,
  )

  useEffect(() => {
    if (mappable.length === 0 || !container.current || map.current) return

    const instance = L.map(container.current, {
      // No default attribution control: the tile layer adds its own, and OSM's
      // licence requires it to be visible.
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

    for (const incident of mappable) {
      const point: L.LatLngExpression = [
        incident.location.latitude as number,
        incident.location.longitude as number,
      ]
      points.push(point)

      const emergency = incident.is_emergency
      const selected = incident.public_ref === selectedRef
      const demo = incident.location.is_synthetic

      // Emergencies read as heavier, not as an alarm. The project's visual
      // language reserves red for genuine errors, and a map where everything
      // shouts is a map nobody triages. A dashed outline — orthogonal to
      // colour — marks a demo/development location without competing with
      // that convention: a marker can be both emergency-coloured and
      // demo-dashed, and each reads independently.
      const marker = L.circleMarker(point, {
        radius: emergency ? 11 : 8,
        weight: selected ? 4 : 2,
        dashArray: demo ? '4 3' : undefined,
        color: selected ? '#1d4ed8' : emergency ? '#b45309' : '#475569',
        fillColor: emergency ? '#f59e0b' : '#94a3b8',
        fillOpacity: 0.85,
      })

      marker.bindTooltip(
        `${incident.public_ref} · ${incident.location.name}${demo ? ' (DEMO)' : ''}`,
        { direction: 'top' },
      )
      marker.on('click', () => onSelect(incident.public_ref))
      // Reachable without a mouse. A map that only responds to clicks excludes
      // keyboard users from the whole feature.
      marker.on('keypress', () => onSelect(incident.public_ref))
      layer.addLayer(marker)
    }

    if (points.length > 0) {
      instance.fitBounds(L.latLngBounds(points).pad(0.25), { maxZoom: 18 })
    }
  }, [mappable, selectedRef, onSelect])

  if (mappable.length === 0) {
    return (
      <div
        className="border-ink-200 bg-ink-50 rounded-xl border border-dashed p-6 text-center"
        data-testid="map-empty-state"
      >
        <h3 className="text-ink-800 text-sm font-medium">
          Campus mapping is not available yet
        </h3>
        <p className="text-ink-600 mx-auto mt-2 max-w-md text-sm leading-relaxed">
          Campus locations have not yet been verified. Incident mapping will become available
          after the campus location survey is completed.
        </p>
        <p className="text-ink-500 mx-auto mt-3 max-w-md text-sm leading-relaxed">
          Every incident below is still anchored to the place the reporter selected, and the
          location name and access notes are shown in full.
        </p>
      </div>
    )
  }

  const hasDemoIncidents = mappable.some((incident) => incident.location.is_synthetic)

  return (
    <div className="space-y-2">
      {hasDemoIncidents && (
        <p
          role="status"
          data-testid="demo-data-notice"
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900"
        >
          One or more incidents below are anchored to a demo campus location — for development
          and testing only, not verified Presidency University data.
        </p>
      )}
      <div
        ref={container}
        role="application"
        aria-label="Campus incident map"
        className="border-ink-200 h-72 w-full overflow-hidden rounded-xl border sm:h-96"
      />
    </div>
  )
}
