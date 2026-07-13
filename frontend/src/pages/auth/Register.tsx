import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { MessageSquare, Phone, Lock, Eye, EyeOff } from 'lucide-react'
import { AuthNavbar } from '@/components/common/AuthNavbar'
import { registerByPhone } from '@/api/auth'
import { useUIStore } from '@/store/uiStore'
import { ButtonLoading } from '@/components/common/Loading'

export function Register() {
  const navigate = useNavigate()
  const { addToast } = useUIStore()

  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!phone || !password || !confirmPassword) {
      addToast({ type: 'error', message: '请填写所有必填项' })
      return
    }

    if (!/^1\d{10}$/.test(phone)) {
      addToast({ type: 'error', message: '请输入正确的11位手机号' })
      return
    }

    if (password !== confirmPassword) {
      addToast({ type: 'error', message: '两次输入的密码不一致' })
      return
    }

    if (password.length < 6) {
      addToast({ type: 'error', message: '密码长度至少6位' })
      return
    }

    setLoading(true)

    try {
      const result = await registerByPhone({ phone, password })

      if (result.success) {
        addToast({ type: 'success', message: '注册成功，请登录' })
        navigate('/login')
      } else {
        addToast({ type: 'error', message: result.message || '注册失败' })
      }
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string; message?: string } } }
      const errorMsg = err?.response?.data?.detail || err?.response?.data?.message || '注册失败，请检查网络连接'
      addToast({ type: 'error', message: errorMsg })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 transition-colors">
      <AuthNavbar />
      <div className="pt-20 pb-10 px-4 sm:px-6 flex items-start justify-center min-h-screen">
      <div className="w-full max-w-md">
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-blue-600 text-white mx-auto mb-4 flex items-center justify-center">
            <MessageSquare className="w-6 h-6" />
          </div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">用户注册</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">创建您的账号以开始使用</p>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="input-group">
              <label className="input-label">手机号</label>
              <div className="relative">
                <Phone className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="请输入11位手机号"
                  maxLength={11}
                  className="input-ios pl-9"
                />
              </div>
            </div>

            <div className="input-group">
              <label className="input-label">密码</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="至少6位字符"
                  className="input-ios pl-9 pr-9"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <div className="input-group">
              <label className="input-label">确认密码</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  className="input-ios pl-9"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full btn-ios-primary"
            >
              {loading ? <ButtonLoading /> : '注 册'}
            </button>
          </form>

          <p className="text-center mt-6 text-slate-500 dark:text-slate-400 text-sm">
            已有账号？{' '}
            <Link to="/login" className="text-blue-600 dark:text-blue-400 font-medium hover:text-indigo-700">
              立即登录
            </Link>
          </p>
        </div>
      </div>
      </div>
    </div>
  )
}
