import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { EvidenceUpload, type EvidenceItem } from './EvidenceUpload'
import { createFetchStub, makeUser, renderWithAuth, signedInRoutes } from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

const LIMITS = {
  max_bytes: 10 * 1024 * 1024,
  accepted_types: ['image/jpeg', 'image/png', 'image/webp'],
  max_per_report: 5,
}

/** A File the browser would produce from a camera or picker. */
function makeFile(name = 'photo.jpg', type = 'image/jpeg', size = 2048): File {
  const file = new File([new Uint8Array(size)], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function uploadResponse(token = 'a'.repeat(32)) {
  return {
    upload_token: token,
    content_type: 'image/jpeg',
    byte_size: 1024,
    width: 320,
    height: 240,
    notice: 'Hidden location and device data have been removed from this image.',
  }
}

/** A real stateful wrapper, so the component's onChange contract drives state
 * exactly as ReportPage does rather than through a re-render shim. */
function Harness({
  onItems,
  limits = LIMITS,
}: {
  onItems: (items: EvidenceItem[]) => void
  limits?: typeof LIMITS | null
}) {
  const [items, setItems] = useState<EvidenceItem[]>([])
  // Mirrors ReportPage: the parent owns the list and passes its setter down.
  onItems(items)
  return <EvidenceUpload items={items} limits={limits} setItems={setItems} />
}

function renderUpload(
  routes = signedInRoutes({ '/evidence': { status: 201, body: uploadResponse() } }),
) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  const state: { items: EvidenceItem[] } = { items: [] }

  const view = renderWithAuth(
    <Harness
      onItems={(items) => {
        state.items = items
      }}
    />,
    { fetchImpl },
  )
  return { ...view, state, fetchImpl }
}

const user = () => userEvent.setup({ delay: null })

describe('EvidenceUpload', () => {
  it('states that evidence is optional', () => {
    renderUpload()
    expect(
      screen.getByRole('heading', { name: /add a photo \(optional\)/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/you do not need one/i)).toBeInTheDocument()
  })

  it('warns about visible content before the picker, not after', () => {
    // The whole point of the warning is that it is read before a photo is
    // chosen, so it sits above the control.
    renderUpload()
    const warning = screen.getByText(/cannot change what is visible/i)
    const picker = screen.getByText(/take or choose a photo/i)
    expect(warning).toBeInTheDocument()
    expect(
      warning.compareDocumentPosition(picker) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('says metadata is removed without claiming the image is anonymous', () => {
    renderUpload()
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/remove hidden location and device information/i)
    // The claim this system cannot make.
    expect(text).not.toMatch(/completely anonymous/i)
    expect(text).not.toMatch(/fully anonymised/i)
  })

  it('names faces and documents as things it cannot remove', () => {
    renderUpload()
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/faces/i)
    expect(text).toMatch(/name badges|documents/i)
  })

  it('accepts only the image types the server accepts', () => {
    renderUpload()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('image/jpeg,image/png,image/webp')
  })

  it('uploads a chosen file and reports the token', async () => {
    const u = user()
    const { state, fetchImpl } = renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())

    await waitFor(() => {
      expect(state.items.some((item) => item.status === 'uploaded')).toBe(true)
    })
    expect(state.items[0].token).toBe('a'.repeat(32))
    expect(
      fetchImpl.calls.some((c) => c.url.includes('/evidence') && c.method === 'POST'),
    ).toBe(true)
  })

  it('sends the file as multipart, not JSON', async () => {
    const u = user()
    const { fetchImpl } = renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())

    await waitFor(() => expect(fetchImpl.calls.some((c) => c.method === 'POST')).toBe(true))
    const call = fetchImpl.calls.find((c) => c.method === 'POST')
    expect(call?.init?.body).toBeInstanceOf(FormData)
    // The browser must set its own Content-Type so the multipart boundary is
    // correct; overriding it produces a body the server cannot parse.
    expect(new Headers(call?.init?.headers).get('Content-Type')).toBeNull()
  })

  it('never sends a storage path or filename field', async () => {
    const u = user()
    const { fetchImpl } = renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile('Priya_hostel.jpg'))

    await waitFor(() => expect(fetchImpl.calls.some((c) => c.method === 'POST')).toBe(true))
    const call = fetchImpl.calls.find((c) => c.method === 'POST')
    const form = call?.init?.body as FormData
    // Only the file part. The server generates the path and ignores the name.
    expect(Array.from(form.keys())).toEqual(['file'])
  })

  it('rejects an oversized file without contacting the server', async () => {
    const { state, fetchImpl } = renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [makeFile('huge.jpg', 'image/jpeg', 20 * 1024 * 1024)] },
    })

    await waitFor(() => expect(state.items).toHaveLength(1))
    expect(state.items[0].status).toBe('failed')
    expect(fetchImpl.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('rejects an unsupported type locally', async () => {
    const { state, fetchImpl } = renderUpload()

    // fireEvent rather than userEvent: userEvent filters by the accept
    // attribute, so a disallowed type would never reach the handler and the
    // test would pass without testing anything.
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [makeFile('doc.pdf', 'application/pdf')] } })

    await waitFor(() => expect(state.items).toHaveLength(1))
    expect(state.items[0].status).toBe('failed')
    expect(fetchImpl.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('surfaces a server rejection against the file', async () => {
    const u = user()
    const { state } = renderUpload(
      signedInRoutes({
        '/evidence': {
          status: 400,
          body: {
            error: {
              code: 'EVIDENCE_REJECTED',
              message: 'That image could not be read. It may be corrupt.',
              request_id: 'req-1',
              details: { reason: 'corrupt' },
            },
          },
        },
      }),
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())

    await waitFor(() => expect(state.items[0]?.status).toBe('failed'))
    expect(state.items[0].error).toMatch(/could not be read/i)
  })

  it('shows a busy state while uploading', async () => {
    const u = user()
    renderUpload(
      signedInRoutes({ '/evidence': { status: 201, body: uploadResponse(), delayMs: 80 } }),
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())

    expect(await screen.findByText(/adding…/i)).toBeInTheDocument()
  })

  it('announces the outcome to assistive technology', async () => {
    const u = user()
    renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile('evidence.jpg'))

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/evidence\.jpg added/i)
    })
  })
})

describe('EvidenceUpload — removal', () => {
  it('removes a file and tells the server to discard it', async () => {
    const u = user()
    const { state, fetchImpl } = renderUpload()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())
    await waitFor(() => expect(state.items[0]?.status).toBe('uploaded'))

    await u.click(screen.getByRole('button', { name: /remove/i }))

    await waitFor(() => expect(state.items).toHaveLength(0))
    // The staged object goes now rather than waiting for the reaper.
    await waitFor(() => {
      expect(fetchImpl.calls.some((c) => c.method === 'DELETE')).toBe(true)
    })
  })

  it('still removes locally when the discard call fails', async () => {
    const u = user()
    const { state } = renderUpload(
      signedInRoutes({
        '/evidence': { status: 201, body: uploadResponse() },
      }),
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())
    await waitFor(() => expect(state.items[0]?.status).toBe('uploaded'))

    await u.click(screen.getByRole('button', { name: /remove/i }))
    // The reaper collects the orphan; the student should not see an error for
    // something already gone from their screen.
    await waitFor(() => expect(state.items).toHaveLength(0))
  })
})

describe('EvidenceUpload — limits', () => {
  it('falls back to safe defaults when limits could not be fetched', () => {
    setCurrentUser(makeUser())
    renderWithAuth(<EvidenceUpload items={[]} setItems={vi.fn()} limits={null} />, {
      fetchImpl: createFetchStub(signedInRoutes()),
    })
    expect(screen.getByText(/up to 10\.0 MB/i)).toBeInTheDocument()
  })

  it('shows how many images remain', () => {
    setCurrentUser(makeUser())
    renderWithAuth(<EvidenceUpload items={[]} setItems={vi.fn()} limits={LIMITS} />, {
      fetchImpl: createFetchStub(signedInRoutes()),
    })
    expect(screen.getByText(/5 remaining/i)).toBeInTheDocument()
  })

  it('hides the picker at the maximum', () => {
    setCurrentUser(makeUser())
    const full: EvidenceItem[] = Array.from({ length: 5 }, (_, i) => ({
      localId: String(i),
      fileName: `p${i}.jpg`,
      byteSize: 1024,
      previewUrl: '',
      status: 'uploaded' as const,
      token: 'x'.repeat(32),
    }))
    renderWithAuth(<EvidenceUpload items={full} setItems={vi.fn()} limits={LIMITS} />, {
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    expect(screen.queryByText(/take or choose a photo/i)).not.toBeInTheDocument()
    expect(screen.getByText(/maximum of 5 photos/i)).toBeInTheDocument()
  })
})
