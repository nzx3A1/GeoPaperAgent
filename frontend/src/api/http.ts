import axios from "axios";

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
  headers: {
    Accept: "application/json",
  },
});

export default http;
