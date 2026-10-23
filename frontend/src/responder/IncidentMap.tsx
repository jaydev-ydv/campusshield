import { useEffect, useRef } from 'react'
import L from 'leaflet'

import type { CampusLocation, IncidentSummary } from '../lib/api'

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
import { googleMapsNavigationUrl } from './navigation'

/** Presidency University campus center coordinates (Rajanukunte, Bengaluru). */
const PRESIDENCY_DEFAULT_CENTER: L.LatLngTuple = [13.16924, 77.534946]
const PRESIDENCY_DEFAULT_ZOOM = 16

interface MappedIncident {
  incident: IncidentSummary
  latitude: number
  longitude: number
  isGps: boolean
  accuracy_m: number | null
  captured_at: string | null
  isSynthetic: boolean
}

function getMappableIncidents(incidents: IncidentSummary[]): MappedIncident[] {
  const result: MappedIncident[] = []

  for (const incident of incidents) {
    // 1. Real device GPS coordinates from emergency SOS signal
    if (
      incident.location_signal?.source === 'device_gps' &&
      incident.location_signal.signal_latitude != null &&
      incident.location_signal.signal_longitude != null &&
      Number.isFinite(incident.location_signal.signal_latitude) &&
      Number.isFinite(incident.location_signal.signal_longitude) &&
      incident.location_signal.signal_latitude >= -90 &&
      incident.location_signal.signal_latitude <= 90 &&
      incident.location_signal.signal_longitude >= -180 &&
      incident.location_signal.signal_longitude <= 180
    ) {
      result.push({
        incident,
        latitude: incident.location_signal.signal_latitude,
        longitude: incident.location_signal.signal_longitude,
        isGps: true,
        accuracy_m: incident.location_signal.accuracy_m ?? null,
        captured_at: incident.location_signal.signal_captured_at ?? null,
        isSynthetic: incident.location.is_synthetic,
      })
      continue
    }

    // 2. Verified surveyed campus location coordinates
    if (
      incident.location.latitude !== null &&
      incident.location.longitude !== null &&
      Number.isFinite(incident.location.latitude) &&
      Number.isFinite(incident.location.longitude) &&
      incident.location.latitude >= -90 &&
      incident.location.latitude <= 90 &&
      incident.location.longitude >= -180 &&
      incident.location.longitude <= 180
    ) {
      result.push({
        incident,
        latitude: incident.location.latitude,
        longitude: incident.location.longitude,
        isGps: false,
        accuracy_m: null,
        captured_at: null,
        isSynthetic: incident.location.is_synthetic,
      })
    }
  }

  return result
}

export function IncidentMap({
  incidents,
  campusLocations = [],
  selectedRef,
  onSelect,
}: {
  incidents: IncidentSummary[]
  campusLocations?: CampusLocation[]
  selectedRef: string | null
  onSelect: (publicRef: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const markerLayer = useRef<L.LayerGroup | null>(null)
  const referenceLayer = useRef<L.LayerGroup | null>(null)

  const mappable = getMappableIncidents(incidents)

  useEffect(() => {
    if (mappable.length === 0 || !container.current || map.current) return

    const instance = L.map(container.current, {
      center: PRESIDENCY_DEFAULT_CENTER,
      zoom: PRESIDENCY_DEFAULT_ZOOM,
      zoomControl: true,
      scrollWheelZoom: false,
    })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors',
    }).addTo(instance)

    referenceLayer.current = L.layerGroup().addTo(instance)
    markerLayer.current = L.layerGroup().addTo(instance)
    map.current = instance

    // Ensure map recalculates its dimensions whenever the container resizes
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined' && container.current) {
      observer = new ResizeObserver(() => {
        instance.invalidateSize()
      })
      observer.observe(container.current)
    }

    return () => {
      observer?.disconnect()
      instance.remove()
      map.current = null
      markerLayer.current = null
      referenceLayer.current = null
    }
  }, [mappable.length])

  useEffect(() => {
    const instance = map.current
    const layer = markerLayer.current
    const refLayer = referenceLayer.current
    if (!instance || !layer) return

    // 1. Draw static campus reference markers in their own layer
    if (refLayer) {
      refLayer.clearLayers()
      const eligibleCampusLocations = campusLocations.filter(
        (loc) => loc.latitude !== null && loc.longitude !== null && !loc.is_synthetic,
      )
      for (const loc of eligibleCampusLocations) {
        const point: L.LatLngTuple = [loc.latitude as number, loc.longitude as number]
        const refMarker = L.circleMarker(point, {
          radius: 5,
          weight: 1.5,
          color: '#64748b',
          fillColor: '#94a3b8',
          fillOpacity: 0.7,
        })
        refMarker.bindTooltip(`${loc.name} (Campus Reference)`, { direction: 'top' })
        refMarker.bindPopup(`
          <div style="font-size: 12px; line-height: 1.4;">
            <div style="font-weight: 600; color: #0f172a;">${loc.name}</div>
            <div style="color: #64748b; font-size: 11px;">Static Campus Reference Point</div>
            ${loc.zone ? `<div style="color: #475569; margin-top: 2px;">Zone: ${loc.zone.name}</div>` : ''}
            <div style="color: #94a3b8; font-size: 10px; margin-top: 4px;">Static Reference: ${loc.latitude!.toFixed(6)}, ${loc.longitude!.toFixed(6)}</div>
          </div>
        `)
        refLayer.addLayer(refMarker)
      }
    }

    // 2. Draw active incident markers
    layer.clearLayers()
    const points: L.LatLngExpression[] = []
    let selectedMarker: L.CircleMarker | null = null

    for (const item of mappable) {
      const { incident, latitude, longitude, isGps, accuracy_m, captured_at, isSynthetic } =
        item
      const point: L.LatLngExpression = [latitude, longitude]
      points.push(point)

      const emergency = incident.is_emergency
      const selected = incident.public_ref === selectedRef

      // If accuracy radius is available, draw an accuracy circle around the GPS coordinate
      if (isGps && accuracy_m !== null && accuracy_m > 0) {
        const accuracyCircle = L.circle(point, {
          radius: accuracy_m,
          weight: 1,
          color: selected ? '#2563eb' : '#d97706',
          dashArray: '3 3',
          fillColor: selected ? '#93c5fd' : '#fde68a',
          fillOpacity: 0.2,
          interactive: false,
        })
        layer.addLayer(accuracyCircle)
      }

      // SOS emergency markers are rendered with a distinct, prominent style.
      const marker = L.circleMarker(point, {
        radius: emergency ? (isGps ? 13 : 11) : 8,
        weight: selected ? 4 : isGps ? 3 : 2,
        dashArray: isSynthetic ? '4 3' : undefined,
        color: selected ? '#1d4ed8' : emergency ? (isGps ? '#b45309' : '#b45309') : '#475569',
        fillColor: emergency ? (isGps ? '#ea580c' : '#f59e0b') : '#94a3b8',
        fillOpacity: 0.9,
      })

      let tooltipText = `${incident.public_ref} · ${isGps ? 'GPS Location' : incident.location.name}${isSynthetic ? ' (DEMO)' : ''}`
      if (accuracy_m !== null && accuracy_m !== undefined) {
        tooltipText += ` · ±${Math.round(accuracy_m)}m accuracy`
      }
      if (captured_at) {
        tooltipText += ` · ${new Date(captured_at).toLocaleTimeString()}`
      }

      const navUrl = googleMapsNavigationUrl(latitude, longitude)
      const popupHtml = `
        <div style="font-size: 12px; line-height: 1.4; min-width: 160px;">
          <div style="font-weight: 600; color: #0f172a; margin-bottom: 2px;">${incident.public_ref}</div>
          <div style="color: #475569; margin-bottom: 4px;">${incident.category?.label ?? 'Emergency Alert'}</div>
          <div style="color: #1e293b; margin-bottom: 4px;">
            ${isGps ? '<strong>GPS:</strong> ' + latitude.toFixed(6) + ', ' + longitude.toFixed(6) : incident.location.name}
            ${accuracy_m ? ' (±' + Math.round(accuracy_m) + 'm)' : ''}
          </div>
          ${captured_at ? '<div style="color: #64748b; font-size: 11px; margin-bottom: 6px;">Captured: ' + new Date(captured_at).toLocaleTimeString() + '</div>' : ''}
          <div style="margin-top: 6px;">
            <a href="${navUrl}" target="_blank" rel="noopener noreferrer" style="color: #1d4ed8; font-weight: 500; text-decoration: underline;">
              Open in Google Maps &rarr;
            </a>
          </div>
        </div>
      `

      marker.bindTooltip(tooltipText, { direction: 'top' })
      marker.bindPopup(popupHtml)
      marker.on('click', () => onSelect(incident.public_ref))
      marker.on('keypress', () => onSelect(incident.public_ref))
      layer.addLayer(marker)

      if (selected) {
        selectedMarker = marker
      }
    }

    // Center map on the selected incident coordinates if one is selected,
    // otherwise fit bounds across all mapped points
    const selectedItem = mappable.find((m) => m.incident.public_ref === selectedRef)
    if (selectedItem) {
      instance.setView(
        [selectedItem.latitude, selectedItem.longitude],
        Math.max(instance.getZoom(), 16),
        { animate: true },
      )
      if (selectedMarker && !selectedMarker.isPopupOpen()) {
        selectedMarker.openPopup()
      }
    } else if (points.length > 0) {
      instance.fitBounds(L.latLngBounds(points).pad(0.25), { maxZoom: 18 })
    }
  }, [mappable, campusLocations, selectedRef, onSelect])

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

  const hasDemoIncidents = mappable.some((item) => item.isSynthetic)

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
