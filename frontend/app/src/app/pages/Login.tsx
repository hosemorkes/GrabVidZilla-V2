import { useState } from "react";
import { useNavigate } from "react-router";
import logoImage from "@/assets/grabvidzilla-logo.png";
import api from "@/api/client";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      // Сохраняем сессию: user_id, name, role
      localStorage.setItem("user", JSON.stringify({ user_id: data.user_id, name: data.name, role: data.role }));
      navigate("/");
    } catch (err: any) {
      if (err.response?.status === 401) {
        setError("Неверный email или пароль");
      } else {
        setError(err.response?.data?.detail || "Ошибка входа. Попробуйте ещё раз.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white flex">
      {/* Left Side - Marketing Content */}
      <div className="flex-1 flex flex-col justify-center items-center px-12">
        <div className="max-w-xl">
          <h1 className="text-4xl mb-4">
            Experience seamless video downloads today!
          </h1>
          <p className="text-xl mb-12">
            Unleash the beast. <em>Grab any video from any platform.</em>
          </p>

          <div className="flex flex-col items-center mb-12">
            <img src={logoImage} alt="GrabVidZilla Logo" className="w-96 h-auto" />
          </div>

          <p className="text-center text-lg">
            Multithreaded power. Streaming? Easy.
          </p>
        </div>
      </div>

      {/* Right Side - Login Form */}
      <div className="flex-1 flex flex-col justify-center items-center px-12">
        <div className="w-full max-w-md">
          <div className="text-right mb-8">
            <button
              onClick={() => navigate("/register")}
              className="text-white hover:underline bg-transparent border-none cursor-pointer"
            >
              Registration
            </button>
          </div>

          <div className="mb-12">
            <h2 className="text-2xl mb-4">
              Because streaming is temporary. Zilla is forever.
            </h2>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <input
              type="text"
              placeholder="Email or username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-6 py-4 rounded-full bg-gray-800 text-white placeholder-gray-400 border-none outline-none"
              required
            />

            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-6 py-4 rounded-full bg-gray-800 text-white placeholder-gray-400 border-none outline-none"
              required
            />

            {error && (
              <p className="text-sm text-center" style={{ color: "#EF4444" }}>
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-4 rounded-full text-black font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
              style={{ backgroundColor: "#00FFB3" }}
            >
              {loading ? "Входим..." : "Enter the Lair"}
            </button>
          </form>

        </div>
      </div>
    </div>
  );
}
