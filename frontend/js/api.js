/* api.js — the contract with the backend. Every fetch() call in the app goes
 * through here, so backend/api.py's routes only have to be matched in one
 * file. EASY to extend: adding a new backend route just means adding one line
 * below that follows the existing pattern — the error handling is shared. */
(function () {
  async function request(path, options) {
    const res = await fetch(path, options);
    const contentType = res.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await res.json().catch(() => ({})) : null;
    if (!res.ok) {
      const message = (data && data.detail) || `${(options && options.method) || "GET"} ${path} failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const get = (path) => request(path);
  const del = (path) => request(path, { method: "DELETE" });
  const post = (path, body, init) => request(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
    ...(init || {}),
  });
  const patch = (path, body) => request(path, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
  });

  window.Api = {
    // workspaces
    listWorkspaces: () => get("/api/workspaces"),
    getWorkspace: (id) => get(`/api/workspaces/${id}`),
    createWorkspace: (unitId, title) => post(`/api/units/${unitId}/workspaces`, { title }),
    deleteWorkspace: (id) => del(`/api/workspaces/${id}`),

    // courses
    listCourses: () => get("/api/courses"),
    getCourse: (id) => get(`/api/courses/${id}`),
    createCourse: (title) => post("/api/courses", { title }),
    deleteCourse: (id) => del(`/api/courses/${id}`),

    // units
    getUnit: (id) => get(`/api/units/${id}`),
    createUnit: (courseId, title) => post(`/api/courses/${courseId}/units`, { title }),
    deleteUnit: (id) => del(`/api/units/${id}`),

    // sources
    addYoutubeSource: (wsId, url) => post(`/api/workspaces/${wsId}/sources`, { url }),
    deleteSource: (wsId, srcId) => del(`/api/workspaces/${wsId}/sources/${srcId}`),
    sourceStreamUrl: (wsId, srcId) => `/api/workspaces/${wsId}/sources/${srcId}/stream`,
    pdfFileUrl: (wsId, srcId) => `/api/workspaces/${wsId}/sources/${srcId}/file`,
    pdfPageImageUrl: (wsId, srcId, page) => `/api/workspaces/${wsId}/sources/${srcId}/pages/${page}/image`,
    uploadPdf: (wsId, formData) => fetch(`/api/workspaces/${wsId}/uploads`, { method: "POST", body: formData })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const err = new Error(data.detail || `Upload failed (${res.status})`);
          err.status = res.status;
          throw err;
        }
        return data;
      }),

    // chat — chatStreamUrl is fetched directly (POST + manual SSE parsing, see chat.js)
    chatStreamUrl: (wsId) => `/api/workspaces/${wsId}/chat`,
    chatVisual: (wsId, message, options) => post(`/api/workspaces/${wsId}/chat/visual`, { message }, options),

    // study guide
    getStudyGuide: (wsId) => get(`/api/workspaces/${wsId}/study-guide`),
    studyGuideStreamUrl: (wsId) => `/api/workspaces/${wsId}/study-guide/stream`,

    // practice quiz
    getPracticeQuiz: (wsId) => get(`/api/workspaces/${wsId}/practice-quiz`),
    practiceStreamUrl: (wsId) => `/api/workspaces/${wsId}/practice-quiz/stream`,
    submitAttempt: (wsId, quizId, questionId, selectedOption) =>
      post(`/api/workspaces/${wsId}/practice-quiz/${quizId}/questions/${questionId}/attempt`, { selected_option: selectedOption }),

    // config (fast/deep model toggle)
    getConfig: () => get("/api/config"),
    patchConfig: (body) => patch("/api/config", body),
  };
})();
