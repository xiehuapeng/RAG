import { defineStore } from 'pinia'

const TOKEN_KEY = 'kb-session-token'
const USER_KEY = 'kb-session-user'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: '',
    user: null,
    authChecked: false,
  }),
  actions: {
    restore() {
      if (!this.token) {
        this.token = localStorage.getItem(TOKEN_KEY) || ''
      }
      if (!this.user) {
        const raw = localStorage.getItem(USER_KEY)
        this.user = raw ? JSON.parse(raw) : null
      }
    },
    async validateSession() {
      this.restore()
      if (!this.token) {
        this.authChecked = true
        return false
      }

      try {
        const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-session-token': this.token,
          },
        })
        if (!response.ok) {
          throw new Error('session invalid')
        }

        const body = await response.json()
        if (body.code !== 0 || !body.data) {
          throw new Error(body.message || 'session invalid')
        }

        this.user = body.data
        localStorage.setItem(USER_KEY, JSON.stringify(body.data))
        this.authChecked = true
        return true
      } catch {
        this.clearSession()
        this.authChecked = true
        return false
      }
    },
    setSession(payload) {
      this.token = payload.token
      this.user = payload.user
      this.authChecked = true
      localStorage.setItem(TOKEN_KEY, payload.token)
      localStorage.setItem(USER_KEY, JSON.stringify(payload.user))
    },
    clearSession() {
      this.token = ''
      this.user = null
      this.authChecked = false
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    },
  },
})
