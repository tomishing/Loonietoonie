import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { errorMessages, scanReceipt, type ScanResult } from '../api/client'
import { prepareImage, toBase64, type PreparedImage } from '../lib/image'
import { recognizeReceipt } from '../ocr'

/** Passed to the review screen (ReceiptFormPage) through router state. */
export interface ScanReviewState {
  scan: ScanResult
  image: Blob
}

type Step =
  | { name: 'pick' }
  | { name: 'scanning'; stage: string; progress: number | null }
  | { name: 'error'; messages: string[] }

// Rough share of the progress bar for each stage (OCR reports its own progress inside the first part).
const READING_SHARE = 0.5

export function ScanPage() {
  const navigate = useNavigate()
  const [image, setImage] = useState<PreparedImage | null>(null)
  // The camera's original file, shown on the scanning screen while the photo is being prepared.
  const [rawPreview, setRawPreview] = useState<string | null>(null)
  const [step, setStep] = useState<Step>({ name: 'pick' })
  // Two inputs: the camera (capture) and the photo gallery. On a computer both open a file picker.
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  // Incremented on cancel or a new photo, so a scan that is still running is ignored when it finishes.
  const runRef = useRef(0)

  useEffect(() => () => (image ? URL.revokeObjectURL(image.previewUrl) : undefined), [image])
  useEffect(() => () => (rawPreview ? URL.revokeObjectURL(rawPreview) : undefined), [rawPreview])
  useEffect(() => () => void (runRef.current += 1), [])

  const scan = async (photo: PreparedImage, run = ++runRef.current) => {
    const current = () => run === runRef.current
    try {
      setStep({ name: 'scanning', stage: 'Reading the text', progress: 0.05 })
      const { text } = await recognizeReceipt(photo.blob, ({ progress }) => {
        if (current()) setStep({ name: 'scanning', stage: 'Reading the text', progress: progress * READING_SHARE })
      })
      if (!current()) return

      setStep({ name: 'scanning', stage: 'Finding the items and prices', progress: null })
      let result = await scanReceipt(text)
      if (!current()) return
      if (result.needs_image) {
        // The text alone didn't add up: let Claude look at the photo once.
        setStep({ name: 'scanning', stage: 'Taking a closer look at the photo', progress: null })
        result = await scanReceipt(text, { base64: await toBase64(photo.blob), mediaType: photo.mediaType })
        if (!current()) return
      }

      const state: ScanReviewState = { scan: result, image: photo.blob }
      navigate('/receipts/new', { state })
    } catch (error) {
      if (current()) setStep({ name: 'error', messages: errorMessages(error) })
    }
  }

  // A new photo goes straight to the scanning screen; there is no separate "Read receipt" step.
  const handleFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const run = ++runRef.current
    setRawPreview(URL.createObjectURL(file))
    setStep({ name: 'scanning', stage: 'Preparing the photo', progress: 0 })
    try {
      const photo = await prepareImage(file)
      if (run !== runRef.current) return
      setImage(photo)
      void scan(photo, run)
    } catch {
      if (run === runRef.current) {
        setStep({ name: 'error', messages: ["Couldn't open that photo. Try a JPEG or PNG image."] })
      }
    }
  }

  const cancel = () => {
    runRef.current += 1
    setStep({ name: 'pick' })
  }

  const inputs = (
    <>
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        onChange={handleFile}
        aria-label="Take a receipt photo"
      />
      <input
        ref={galleryRef}
        type="file"
        accept="image/*"
        className="sr-only"
        onChange={handleFile}
        aria-label="Choose a receipt photo"
      />
    </>
  )

  const scanningPreview = rawPreview ?? image?.previewUrl
  if (step.name === 'scanning' && scanningPreview) {
    return (
      <section className="page scanning" aria-busy="true">
        <div className="scanning-photo">
          <img src={scanningPreview} alt="Receipt being scanned" />
          <span className="scan-line" aria-hidden="true" />
        </div>
        <div className="scanning-text" role="status" aria-live="polite">
          <img className="scanning-mascot" src="/favicon.svg" alt="" width={48} height={48} />
          <h1>Scanning your receipt…</h1>
          <p className="muted">{step.stage}…</p>
          <div
            className={`scan-progress${step.progress === null ? ' scan-progress-busy' : ''}`}
            role="progressbar"
            aria-label={step.stage}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={step.progress === null ? undefined : Math.round(step.progress * 100)}
          >
            <span style={step.progress === null ? undefined : { width: `${Math.max(4, step.progress * 100)}%` }} />
          </div>
        </div>
        <button type="button" className="button button-ghost" onClick={cancel}>
          Cancel
        </button>
      </section>
    )
  }

  return (
    <section className="page">
      <div className="page-title">
        <h1>Scan a receipt</h1>
      </div>

      <div className="scan">
        {image ? (
          <img className="scan-preview" src={image.previewUrl} alt="Receipt photo" />
        ) : (
          <button type="button" className="scan-placeholder" onClick={() => cameraRef.current?.click()}>
            <span aria-hidden="true">📷</span>
            Take a photo of a receipt
          </button>
        )}
        {inputs}

        {step.name === 'error' &&
          step.messages.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}

        <div className="form-actions">
          {image && step.name === 'error' && (
            <button type="button" className="button button-primary" onClick={() => void scan(image)}>
              Try again
            </button>
          )}
          <button
            type="button"
            className={`button ${image ? 'button-secondary' : 'button-primary'}`}
            onClick={() => cameraRef.current?.click()}
          >
            📷 {image ? 'Retake' : 'Take photo'}
          </button>
          <button type="button" className="button button-secondary" onClick={() => galleryRef.current?.click()}>
            🖼️ Choose from gallery
          </button>
          <Link to="/receipts/new" className="button button-ghost">
            Enter by hand
          </Link>
        </div>

        <p className="muted field-note">
          Scanning starts as soon as you take or choose a photo. The text is read on your device; only the text goes
          to the server, plus the photo if the first reading doesn't add up. You'll check everything before saving.
        </p>
      </div>
    </section>
  )
}
