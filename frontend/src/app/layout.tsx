import type { Metadata } from "next";
import { Hubot_Sans, Martian_Mono, Mona_Sans } from "next/font/google";
import { Header } from "@/components/Header";
import { ShowcaseBanner } from "@/components/ShowcaseBanner";
import { StreamProvider } from "@/components/StreamProvider";
import "./globals.css";

// The portfolio's type: GitHub's own pair, plus Martian Mono for data.
const hubot = Hubot_Sans({ variable: "--font-hubot", subsets: ["latin"] });
const mona = Mona_Sans({ variable: "--font-mona", subsets: ["latin"] });
const martian = Martian_Mono({ variable: "--font-martian", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Agent Desk",
  description: "Watch Patch manage your GitHub: every step, every request, every proposal.",
};

// Runs while the HTML is parsed, so the theme applies before the first paint: the saved choice, otherwise light.
const THEME_SCRIPT = `(function(){var t;try{t=localStorage.getItem("desk-theme")}catch(e){}document.documentElement.setAttribute("data-theme",t==="dark"?"dark":"light")})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${hubot.variable} ${mona.variable} ${martian.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <StreamProvider>
          <Header />
          <ShowcaseBanner />
          <main className="mx-auto max-w-[1240px] px-4 pb-24 pt-8 sm:px-8">{children}</main>
        </StreamProvider>
      </body>
    </html>
  );
}
