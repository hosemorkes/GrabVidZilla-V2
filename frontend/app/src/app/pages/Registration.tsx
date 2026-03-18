import { useState } from "react";
import { useNavigate } from "react-router";
import logoImage from "@/assets/grabvidzilla-logo.png";
import api from "@/api/client";

export default function Registration() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeatPassword, setRepeatPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password !== repeatPassword) {
      setError("Пароли не совпадают");
      return;
    }
    if (password.length < 3) {
      setError("Пароль должен быть не короче 3 символов");
      return;
    }

    setLoading(true);
    try {
      await api.post("/auth/register", { email, name, password });
      // После успешной регистрации → страница входа
      navigate("/login");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Ошибка регистрации. Попробуйте ещё раз.");
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

      {/* Right Side - Registration Form */}
      <div className="flex-1 flex flex-col justify-center items-center px-12">
        <div className="w-full max-w-md">
          <div className="text-right mb-8">
            <button
              onClick={() => navigate("/login")}
              className="text-white hover:underline bg-transparent border-none cursor-pointer"
            >
              Already a user? Sign in.
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
              placeholder="Your name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-6 py-4 rounded-full bg-gray-800 text-white placeholder-gray-400 border-none outline-none"
              required
            />

            <input
              type="email"
              placeholder="Your email address"
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

            <input
              type="password"
              placeholder="Repeat password"
              value={repeatPassword}
              onChange={(e) => setRepeatPassword(e.target.value)}
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
              {loading ? "Создаём аккаунт..." : "Enter the Lair"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
