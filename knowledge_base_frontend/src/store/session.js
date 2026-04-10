import { defineStore } from 'pinia'

const TOKEN_KEY = 'kb-session-token'
const USER_KEY = 'kb-session-user'

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: '',
    user: null,
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
    setSession(payload) {
      this.token = payload.token
      this.user = payload.user
      localStorage.setItem(TOKEN_KEY, payload.token)
      localStorage.setItem(USER_KEY, JSON.stringify(payload.user))
    },
    clearSession() {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    },
  },
})
