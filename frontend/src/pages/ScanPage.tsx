import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { errorMessages, scanReceipt, type ScanResult } from '../api/client'
import { hasDocumentScanner, scanDocument, ScannerUnavailableError } from '../capture/documentScanner'
import { Icon } from '../components/Icon'
import { prepareImage, stitchImages, toBase64, type PreparedImage } from '../lib/image'
import { recognizeReceiptParts } from '../ocr'
import { isPdf, MAX_PDF_PAGES, PdfError, readPdf } from '../capture/pdf'

/** Passed to the review screen (ReceiptFormPage) through router state. */
export interface ScanReviewState {
  scan: ScanResult
  image: Blob
}

type Step =
  | { name: 'pick' }
  | { name: 'scanning'; stage: string; progress: number | null }
  | { name: 'error'; messages: string[] }

// "Take photo" is offered in the Android app (document scanner). The website only picks a photo
// from the gallery or files.
const canTakePhoto = hasDocumentScanner

// Rough share of the progress bar for each stage (OCR reports its own progress inside the first part).
const READING_SHARE = 0.5

/** The prepared photo(s): one per part of a long receipt, plus the image shown and saved. */
interface Photos {
  pages: PreparedImage[]
  /** The single page, or all pages stitched top to bottom. */
  combined: PreparedImage
  /** PDF only: each page's embedded text, used instead of OCR where present. */
  texts?: string[]
}

function revokePhotos(photos: Photos) {
  for (const url of new Set([...photos.pages.map((page) => page.previewUrl), photos.combined.previewUrl])) {
    URL.revokeObjectURL(url)
  }
}

export function ScanPage() {
  const navigate = useNavigate()
  const [photos, setPhotos] = useState<Photos | null>(null)
  // The camera's original file (the first part), shown on the scanning screen while photos are prepared.
  const [rawPreview, setRawPreview] = useState<string | null>(null)
  const [step, setStep] = useState<Step>({ name: 'pick' })
  // Two inputs: the camera (capture) and the photo gallery. On a computer both open a file picker.
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  // Incremented on cancel or a new photo, so a scan that is still running is ignored when it finishes.
  const runRef = useRef(0)
  const scannerUnavailable = useRef(false)

  useEffect(() => () => (photos ? revokePhotos(photos) : undefined), [photos])
  useEffect(() => () => (rawPreview ? URL.revokeObjectURL(rawPreview) : undefined), [rawPreview])
  useEffect(() => () => void (runRef.current += 1), [])

  const scan = async ({ pages, combined, texts }: Photos, run = ++runRef.current) => {
    const current = () => run === runRef.current
    const reading = (part: number) => (pages.length > 1 ? `Reading the text (part ${part} of ${pages.length})` : 'Reading the text')
    try {
      setStep({ name: 'scanning', stage: reading(1), progress: 0.05 })
      const { text } = await recognizeReceiptParts(
        pages.map((page) => page.blob),
        ({ progress, part }) => {
          if (current()) setStep({ name: 'scanning', stage: reading(part), progress: progress * READING_SHARE })
        },
        texts,
      )
      if (!current()) return

      setStep({ name: 'scanning', stage: 'Finding the items and prices', progress: null })
      let result = await scanReceipt(text)
      if (!current()) return
      if (result.needs_image) {
        // The text alone didn't add up: let Claude look at the photo(s) once.
        setStep({
          name: 'scanning',
          stage: pages.length > 1 ? 'Taking a closer look at the photos' : 'Taking a closer look at the photo',
          progress: null,
        })
        const images = await Promise.all(
          pages.map(async (page) => ({ base64: await toBase64(page.blob), mediaType: page.mediaType })),
        )
        result = await scanReceipt(text, images)
        if (!current()) return
      }

      const state: ScanReviewState = { scan: result, image: combined.blob }
      navigate('/receipts/new', { state })
    } catch (error) {
      if (current()) setStep({ name: 'error', messages: errorMessages(error) })
    }
  }

  // A new photo goes straight to the scanning screen; there is no separate "Read receipt" step.
  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) void (isPdf(file) ? handlePdf(file) : handlePhotos([file]))
  }

  /** A PDF receipt: its pages go through the same steps as a multi-page scan. */
  const handlePdf = async (file: File) => {
    const run = ++runRef.current
    setRawPreview(null)
    setStep({ name: 'scanning', stage: 'Opening the PDF', progress: 0 })
    try {
      const pdf = await readPdf(file)
      if (run !== runRef.current) return
      if (pdf.pageCount > MAX_PDF_PAGES) {
        setStep({
          name: 'error',
          messages: [
            `This PDF has ${pdf.pageCount} pages; up to ${MAX_PDF_PAGES} can be read. Save just the receipt's pages as a PDF and try again.`,
          ],
        })
        return
      }
      if (pdf.images.length === 0) throw new PdfError('This PDF has no pages.')
      await handlePhotos(pdf.images, pdf.texts, run)
    } catch (error) {
      if (run === runRef.current) {
        setStep({ name: 'error', messages: [error instanceof PdfError ? error.message : "Couldn't open that PDF."] })
      }
    }
  }

  /** files: one photo, or the parts of a long receipt from the document scanner (top to bottom). */
  const handlePhotos = async (files: Blob[], texts?: string[], run = ++runRef.current) => {
    setRawPreview(URL.createObjectURL(files[0]))
    setStep({ name: 'scanning', stage: files.length > 1 ? 'Preparing the photos' : 'Preparing the photo', progress: 0 })
    try {
      const pages: PreparedImage[] = []
      for (const file of files) pages.push(await prepareImage(file)) // one at a time: less memory
      const combined = pages.length > 1 ? await stitchImages(pages) : pages[0]
      if (run !== runRef.current) return
      const prepared = { pages, combined, texts }
      setPhotos(prepared)
      void scan(prepared, run)
    } catch {
      if (run === runRef.current) {
        setStep({ name: 'error', messages: ["Couldn't open that photo. Try a JPEG or PNG image."] })
      }
    }
  }

  // Android app: Google's document scanner (crops and straightens the receipt; a long receipt can be
  // several pages). Web, or a phone without the scanner: the camera through a file input.
  const takePhoto = async () => {
    if (!hasDocumentScanner || scannerUnavailable.current) {
      cameraRef.current?.click()
      return
    }
    try {
      const scanned = await scanDocument()
      if (scanned) void handlePhotos(scanned)
    } catch (error) {
      if (error instanceof ScannerUnavailableError) {
        scannerUnavailable.current = true
        cameraRef.current?.click()
      } else {
        setStep({ name: 'error', messages: ["Couldn't open the scanner. Try again, or choose a photo from the gallery."] })
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
        accept="image/*,application/pdf"
        className="sr-only"
        onChange={handleFile}
        aria-label="Choose a receipt photo or PDF"
      />
    </>
  )

  const scanningPreview = rawPreview ?? photos?.combined.previewUrl
  if (step.name === 'scanning') {
    return (
      <section className="page scanning" aria-busy="true">
        {scanningPreview && (
          <div className="scanning-photo">
            <img src={scanningPreview} alt="Receipt being scanned" />
            <span className="scan-line" aria-hidden="true" />
          </div>
        )}
        <div className="scanning-text" role="status" aria-live="polite">
          <img className="scanning-mascot" src="/loon-badge.png" alt="" width={48} height={48} />
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
        {photos ? (
          <img className="scan-preview" src={photos.combined.previewUrl} alt="Receipt photo" />
        ) : (
          <button
            type="button"
            className="scan-placeholder"
            onClick={() => (canTakePhoto ? void takePhoto() : galleryRef.current?.click())}
          >
            <span className="scan-placeholder-icon">
              <Icon name={canTakePhoto ? 'camera' : 'image'} size={40} />
            </span>
            <strong>{canTakePhoto ? 'Take a photo of a receipt' : 'Choose a photo or PDF of a receipt'}</strong>
            <span className="muted">
              {canTakePhoto
                ? 'Lay it flat, in good light, with all four corners showing.'
                : 'A clear, straight photo with all four corners showing reads best.'}
            </span>
          </button>
        )}
        {inputs}

        {step.name === 'error' &&
          step.messages.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}

        <div className="scan-actions">
          {photos && step.name === 'error' && (
            <button type="button" className="button button-primary button-block" onClick={() => void scan(photos)}>
              Try again
            </button>
          )}
          {canTakePhoto && (
            <button
              type="button"
              className={`button button-block ${photos ? 'button-secondary' : 'button-primary'}`}
              onClick={() => void takePhoto()}
            >
              <Icon name="camera" size={20} />
              {photos ? 'Retake' : 'Take photo'}
            </button>
          )}
          <button
            type="button"
            className={`button button-block ${canTakePhoto || photos ? 'button-secondary' : 'button-primary'}`}
            onClick={() => galleryRef.current?.click()}
          >
            <Icon name="image" size={20} />
            Choose photo or PDF
          </button>
          <Link to="/receipts/new" className="button button-ghost button-block">
            <Icon name="pencil" size={20} />
            Enter by hand
          </Link>
        </div>

        <p className="privacy-note">
          <Icon name="lock" size={16} />
          <span>
            The text is read on your device; only the text goes to the server, plus the photo if the first reading
            doesn't add up. You'll check everything before saving.
          </span>
        </p>
      </div>
    </section>
  )
}
