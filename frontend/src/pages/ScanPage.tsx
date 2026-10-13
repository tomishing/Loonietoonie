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
  | { name: 'reading'; progress: number; stage: string }
  | { name: 'asking'; withPhoto: boolean }
  | { name: 'error'; messages: string[] }

export function ScanPage() {
  const navigate = useNavigate()
  const [image, setImage] = useState<PreparedImage | null>(null)
  const [step, setStep] = useState<Step>({ name: 'pick' })
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => () => (image ? URL.revokeObjectURL(image.previewUrl) : undefined), [image])

  const handleFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      setImage(await prepareImage(file))
      setStep({ name: 'pick' })
    } catch {
      setStep({ name: 'error', messages: ["Couldn't open that photo. Try a JPEG or PNG image."] })
    }
  }

  const handleScan = async () => {
    if (!image) return
    try {
      setStep({ name: 'reading', progress: 0, stage: 'Starting the text reader' })
      const { text } = await recognizeReceipt(image.blob, ({ progress, stage }) =>
        setStep({ name: 'reading', progress, stage }),
      )

      setStep({ name: 'asking', withPhoto: false })
      let result = await scanReceipt(text)
      if (result.needs_image) {
        // The text alone didn't add up: let Claude look at the photo once.
        setStep({ name: 'asking', withPhoto: true })
        result = await scanReceipt(text, { base64: await toBase64(image.blob), mediaType: image.mediaType })
      }

      const state: ScanReviewState = { scan: result, image: image.blob }
      navigate('/receipts/new', { state })
    } catch (error) {
      setStep({ name: 'error', messages: errorMessages(error) })
    }
  }

  const busy = step.name === 'reading' || step.name === 'asking'

  return (
    <section className="page">
      <div className="page-title">
        <h1>Scan a receipt</h1>
      </div>

      <div className="scan">
        {image ? (
          <img className="scan-preview" src={image.previewUrl} alt="Receipt photo" />
        ) : (
          <button type="button" className="scan-placeholder" onClick={() => inputRef.current?.click()}>
            <span aria-hidden="true">📷</span>
            Take a photo or choose one
          </button>
        )}

        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          onChange={handleFile}
          aria-label="Receipt photo"
        />

        {step.name === 'reading' && (
          <div className="scan-status" role="status">
            <p>{step.stage}…</p>
            <progress max={1} value={step.progress} />
          </div>
        )}
        {step.name === 'asking' && (
          <div className="scan-status" role="status">
            <p>{step.withPhoto ? 'Taking a closer look at the photo…' : 'Working out the items and prices…'}</p>
            <progress />
          </div>
        )}
        {step.name === 'error' &&
          step.messages.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}

        <div className="form-actions">
          {image && (
            <button type="button" className="button button-primary" onClick={handleScan} disabled={busy}>
              {busy ? 'Reading…' : 'Read receipt'}
            </button>
          )}
          <button
            type="button"
            className="button button-secondary"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            {image ? 'Choose another photo' : 'Choose photo'}
          </button>
          <Link to="/receipts/new" className="button button-ghost">
            Enter by hand
          </Link>
        </div>

        <p className="muted field-note">
          The text is read on your device. Only the text goes to the server, plus the photo if the
          first reading doesn't add up. You'll check everything before saving.
        </p>
      </div>
    </section>
  )
}
