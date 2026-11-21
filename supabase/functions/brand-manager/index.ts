import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { SignJWT, importPKCS8 } from "https://esm.sh/jose@4.14.4";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

async function getGoogleDriveAccessToken(saJsonStr: string) {
  const sa = JSON.parse(saJsonStr);
  const privateKey = await importPKCS8(sa.private_key, "RS256");
  const jwt = await new SignJWT({ scope: "https://www.googleapis.com/auth/drive" })
    .setProtectedHeader({ alg: "RS256" })
    .setIssuer(sa.client_email)
    .setAudience("https://oauth2.googleapis.com/token")
    .setIssuedAt()
    .setExpirationTime("1h")
    .sign(privateKey);

  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: jwt,
    }),
  }).then((r) => r.json());
  return tokenRes.access_token;
}

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });

  try {
    const supabaseAdmin = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );

    const authHeader = req.headers.get("Authorization");
    if (!authHeader) throw new Error("Yetkilendirme başlığı eksik.");

    const userClient = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_ANON_KEY")!,
      { global: { headers: { Authorization: authHeader } } }
    );
    const { data: { user } } = await userClient.auth.getUser();
    if (!user?.email) throw new Error("Geçersiz oturum.");

    const { data: authRecord } = await supabaseAdmin
      .from("authorized_users")
      .select("role")
      .eq("email", user.email)
      .single();

    if (!authRecord?.role || !["founder", "editor"].includes(authRecord.role)) {
      throw new Error("Bu işlem için yetkili kullanıcı olmanız gerekir.");
    }

    const body = await req.json();
    const { action } = body;

    if (action === "optimize_content") {
      const geminiKey = Deno.env.get("GEMINI_API_KEY")?.trim();
      if (!geminiKey) throw new Error("GEMINI_API_KEY tanımlı değil.");
      const { niche_keywords, file_name, current_title } = body;
      const prompt = `
        Sen viral sosyal medya içerik uzmanısın. Aşağıdaki dosya adı ve taslak başlığa göre;
        Marka Nişi: ${niche_keywords || "genel, trend"}
        Dosya: ${file_name || ""} | Taslak: ${current_title || ""}

        1. YouTube için maksimum 70 karakterlik yüksek tıklanma oranlı Türkçe başlık yaz.
        2. Instagram ve YouTube için ilk cümlesi merak uyandıran (125 karakter kancalı) açıklama ve sonuna 6 adet güncel Türkçe/niş hashtag ekle.
        3. İzleyiciyi yoruma teşvik edecek samimi bir "İlk Yorum" sorusu yaz.

        SADECE JSON DÖNDÜR: {"title": "...", "description": "...", "first_comment": "..."}
      `;
      const models = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"];
      let lastErr = "";
      for (const model of models) {
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents: [{ parts: [{ text: prompt }] }],
              generationConfig: { responseMimeType: "application/json" },
            }),
          }
        ).then((r) => r.json());
        if (res?.candidates?.[0]?.content?.parts?.[0]?.text) {
          let raw = res.candidates[0].content.parts[0].text.trim();
          if (raw.startsWith("```")) {
            raw = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
          }
          const aiData = JSON.parse(raw);
          return new Response(JSON.stringify({ success: true, data: aiData, model }), {
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          });
        }
        lastErr = `${model}: ${JSON.stringify(res)}`;
      }
      throw new Error("Gemini API hatası: " + lastErr);
    }

    if (authRecord.role !== "founder") {
      throw new Error("Bu işlem için sadece Kurucu (Founder) yetkilidir.");
    }

    if (action === "create_group") {
      const { group_name, niche_keywords, manager_email } = body;
      const folderName = group_name.trim().replace(/[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ_-]/g, "_");
      const driveToken = await getGoogleDriveAccessToken(Deno.env.get("GOOGLE_SERVICE_ACCOUNT_JSON")!.trim());
      const queueRootId = Deno.env.get("DRIVE_QUEUE_ROOT_ID")!.trim();

      const driveRes = await fetch("https://www.googleapis.com/drive/v3/files", {
        method: "POST",
        headers: { Authorization: `Bearer ${driveToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          name: folderName,
          mimeType: "application/vnd.google-apps.folder",
          parents: [queueRootId],
        }),
      }).then((r) => r.json());

      if (!driveRes.id) throw new Error("Drive klasörü oluşturulamadı: " + JSON.stringify(driveRes));

      const { data, error } = await supabaseAdmin
        .from("channel_groups")
        .insert({
          group_name: group_name.trim(),
          drive_folder_id: driveRes.id,
          drive_folder_name: folderName,
          niche_keywords: niche_keywords || "genel, trend",
          manager_email: manager_email || user.email,
        })
        .select()
        .single();

      if (error) throw error;
      return new Response(JSON.stringify({ success: true, group: data }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "delete_group") {
      const { group_id, drive_folder_id } = body;
      const driveToken = await getGoogleDriveAccessToken(Deno.env.get("GOOGLE_SERVICE_ACCOUNT_JSON")!.trim());
      await fetch(`https://www.googleapis.com/drive/v3/files/${drive_folder_id}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${driveToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({ trashed: true }),
      });
      const { error } = await supabaseAdmin.from("channel_groups").delete().eq("id", group_id);
      if (error) throw error;
      return new Response(JSON.stringify({ success: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "connect_youtube") {
      const { group_id, code, redirect_uri } = body;
      const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          code,
          client_id: Deno.env.get("GOOGLE_CLIENT_ID")!.trim(),
          client_secret: Deno.env.get("GOOGLE_CLIENT_SECRET")!.trim(),
          redirect_uri,
          grant_type: "authorization_code",
        }),
      }).then((r) => r.json());

      if (!tokenRes.refresh_token) throw new Error("Refresh token alınamadı.");

      const ytRes = await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {
        headers: { Authorization: `Bearer ${tokenRes.access_token}` },
      }).then((r) => r.json());

      const channelName = ytRes.items?.[0]?.snippet?.title || "YouTube Kanalı";

      const { error } = await supabaseAdmin
        .from("channel_groups")
        .update({ yt_channel_name: channelName, yt_refresh_token: tokenRes.refresh_token, yt_connected: true })
        .eq("id", group_id);

      if (error) throw error;
      return new Response(JSON.stringify({ success: true, channel_name: channelName }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "exchange_instagram_code") {
      const { code, redirect_uri } = body;
      const appId = Deno.env.get("META_APP_ID")!.trim();
      const appSecret = Deno.env.get("META_APP_SECRET")!.trim();

      const shortRes = await fetch(
        `https://graph.facebook.com/v21.0/oauth/access_token?client_id=${appId}&redirect_uri=${encodeURIComponent(redirect_uri)}&client_secret=${appSecret}&code=${code}`
      ).then((r) => r.json());

      if (!shortRes.access_token) {
        throw new Error("Meta kısa token hatası: " + JSON.stringify(shortRes));
      }

      const longRes = await fetch(
        `https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=${appId}&client_secret=${appSecret}&fb_exchange_token=${shortRes.access_token}`
      ).then((r) => r.json());

      const finalUserToken = longRes.access_token || shortRes.access_token;

      const pagesRes = await fetch(
        `https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,instagram_business_account{id,username},connected_instagram_account{id,username}&access_token=${finalUserToken}`
      ).then((r) => r.json());

      if (pagesRes.error) {
        throw new Error("Meta sayfalar hatası: " + JSON.stringify(pagesRes.error));
      }

      const pages = pagesRes.data || [];
      const igAccounts = pages
        .filter((p: any) => p.instagram_business_account || p.connected_instagram_account)
        .map((p: any) => {
          const ig = p.instagram_business_account || p.connected_instagram_account;
          return {
            page_name: p.name,
            ig_business_id: ig.id,
            ig_username: ig.username || p.name,
            access_token: p.access_token,
          };
        });

      return new Response(
        JSON.stringify({
          success: true,
          accounts: igAccounts,
          pages_found: pages.map((p: any) => p.name),
        }),
        {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    if (action === "save_instagram_to_group") {
      const { group_id, ig_username, ig_business_id, access_token } = body;
      const expiresAt = new Date(Date.now() + 59 * 24 * 3600 * 1000).toISOString();
      const { error } = await supabaseAdmin
        .from("channel_groups")
        .update({ ig_username, ig_business_id, ig_access_token: access_token, ig_token_expires_at: expiresAt, ig_connected: true })
        .eq("id", group_id);

      if (error) throw error;
      return new Response(JSON.stringify({ success: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    throw new Error("Geçersiz işlem.");
  } catch (err: any) {
    return new Response(JSON.stringify({ success: false, error: err.message }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
