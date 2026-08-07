FROM nginx:1.27-alpine

# nginx:alpine ships an "nginx" user (uid 101); run as it rather than root.
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html
COPY images/    /usr/share/nginx/html/images/

# The stock image's default vhost would collide with ours on 8080.
RUN rm -f /etc/nginx/conf.d/default.conf \
 && chown -R nginx:nginx /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
