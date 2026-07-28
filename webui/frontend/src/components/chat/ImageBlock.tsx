import { useState } from 'react'
import { ImageLightbox } from '../common/ImageLightbox'

interface ImageBlockProps {
  src: string
  alt?: string
}

export function ImageBlock({ src, alt }: ImageBlockProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <img
        src={src}
        alt={alt || ''}
        className="my-2 max-w-full cursor-pointer rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
        onClick={() => setOpen(true)}
      />
      <ImageLightbox src={src} alt={alt} open={open} onOpenChange={setOpen} />
    </>
  )
}
