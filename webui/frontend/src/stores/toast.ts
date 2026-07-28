import { create } from 'zustand'

export type ToastType = 'info' | 'success' | 'error' | 'warning'

export interface Toast {
  id: string
  type: ToastType
  message: string
  duration?: number
}

interface ToastState {
  toasts: Toast[]
  addToast: (type: ToastType, message: string, duration?: number) => void
  removeToast: (id: string) => void
}

let toastId = 0

export const useToastStore = create<ToastState>()((set) => ({
  toasts: [],
  addToast: (type, message, duration = 5000) => {
    const id = `toast-${++toastId}`
    set((state) => ({
      toasts: [...state.toasts.slice(-2), { id, type, message, duration }],
    }))
    if (duration > 0) {
      setTimeout(() => {
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        }))
      }, duration)
    }
  },
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
}))
