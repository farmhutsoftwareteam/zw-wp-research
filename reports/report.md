# Zimbabwe WordPress sites — research report

_Generated: 2026-05-01 12:37 UTC_

- Total WP-positive domains analyzed: **1023**
- Verified with Playwright: **194**
- Categorized with Claude (Haiku): **1023**

## Methodology

1. Seed harvest: Tranco top-1M (.zw filter) + Common Crawl CDX + curated scrapes (techzim.co.zw, pindula.co.zw, gov.zw).
2. DNS resolution against 1.1.1.1 / 8.8.8.8; CDN tagging by IP CIDR; parking-IP filter.
3. WordPress detection: 5 probe paths, 12 weighted signals, threshold score >= 70.
4. Traffic enrichment: Tranco rank; optional Cloudflare Radar bucket.
5. Categorization: Claude Haiku via `claude -p` (Max plan), batched 20.
6. Verification: Playwright headless render at 1440×900, screenshot + asset URL fingerprinting.

## Categories

| Category | Count |
| --- | --- |
| business | 538 |
| education | 108 |
| ecommerce | 89 |
| ngo | 76 |
| other | 69 |
| government | 68 |
| news | 34 |
| blog | 28 |
| religious | 13 |

### Business — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [utande.co.zw](https://utande.co.zw/) | 40257 | 100 | hello-elementor | elementor, elementor-pro, safe-svg |
| [zimswitch.co.zw](https://zimswitch.co.zw/) | 268007 | 100 | zimswitch | ewww-image-optimizer |
| [delta.co.zw](https://delta.co.zw/) | 559371 | 100 | delta-zimbabwe | age-gate, codevz-plus, contact-form-7 +9 |
| [hansole.co.zw](https://hansole.co.zw/) | 914509 | 100 | astra | astra-sites, elementor, latepoint +2 |
| [pay.maz.co.zw](https://pay.maz.co.zw/) | — | 100 | MAZ | contact-form-7, woocommerce |
| [sbminnovations.co.zw](https://sbminnovations.co.zw/) | — | 100 | astra | click-to-chat-for-whatsapp, elementor |
| [oxfordproperties.co.zw](https://oxfordproperties.co.zw/) | — | 100 | enfold |  |
| [avenuesclinic.co.zw](https://avenuesclinic.co.zw/) | — | 100 | enfold | alert-notice-boxes, click-to-chat-for-whatsapp, woo-donations +3 |
| [sayspring.co.zw](https://sayspring.co.zw/) | — | 100 | twentytwenty |  |
| [sanitationservices.co.zw](https://sanitationservices.co.zw/) | — | 85 | Divi | animate-it, contact-form-7, gs-logo-slider |

### Education — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [zou.ac.zw](https://zou.ac.zw/) | 115329 | 100 | — |  |
| [gzu.ac.zw](https://gzu.ac.zw/) | 564987 | 100 | astra | astra-addon, astra-pro-sites, download-manager +7 |
| [high.abbeys.co.zw](https://high.abbeys.co.zw/) | — | 100 | popularfx | cookieadmin, cookieadmin-pro, pagelayer +3 |
| [preschool.abbeys.co.zw](https://preschool.abbeys.co.zw/) | — | 100 | popularfx | cookieadmin, cookieadmin-pro, pagelayer +2 |
| [vocational.abbeys.co.zw](https://vocational.abbeys.co.zw/) | — | 100 | popularfx | cookieadmin, cookieadmin-pro, pagelayer +2 |
| [prep.abbeys.co.zw](https://prep.abbeys.co.zw/) | — | 100 | popularfx | cookieadmin, cookieadmin-pro, pagelayer +3 |
| [vicfallsprimary.co.zw](https://vicfallsprimary.co.zw/) | — | 93 | enfold1 | CuteSlider, bookly-responsive-appointment-booking-tool, google-calendar-events +3 |
| [icd.co.zw](https://icd.co.zw/) | — | 100 | astra | click-to-chat-for-whatsapp, elementor, elementskit-lite +5 |
| [zie.co.zw](https://zie.co.zw/) | — | 100 | zimie | contact-form-7, fancybox-for-wordpress, the-events-calendar +1 |
| [journal.identityconsultancy.co.zw](https://journal.identityconsultancy.co.zw/) | — | 100 | soledad | Journal-Research-Publication, citations, elementor +16 |

### Ecommerce — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [powershop.co.zw](https://powershop.co.zw/) | 388827 | 100 | blonwe | ar-contactus, blonwe-core, contact-form-7 +6 |
| [tvsales.co.zw](https://tvsales.co.zw/) | 832529 | 100 | hello-elementor | 3d-flipbook-dflip-lite, add-search-to-menu, elementor +13 |
| [masseyferguson.co.zw](https://masseyferguson.co.zw/) | — | 75 | backhoe | contact-form-7, js_composer, woocomm-product-enquiry +2 |
| [byco.co.zw](https://byco.co.zw/) | — | 95 | Avada | eazyest-gallery, js_composer |
| [groceries.vegetablebasket.co.zw](https://groceries.vegetablebasket.co.zw/) | — | 100 | shopire | fable-extra, jetpack, woocommerce +1 |
| [khalidarealty.co.zw](https://khalidarealty.co.zw/) | — | 100 | hello-elementor | addonskit-for-elementor, directorist, elementor +3 |
| [shop.zifa.co.zw](https://shop.zifa.co.zw/) | — | 100 | zifa_shop_theme_87 | woocommerce |
| [nashfurnishers.co.zw](https://nashfurnishers.co.zw/) | — | 100 | nooni | contact-form-7, elementor, enterprise-ecommerce +4 |
| [abbmotorspares.co.zw](https://abbmotorspares.co.zw/) | — | 100 | abb | ewww-image-optimizer |
| [kitchenlink.co.zw](https://kitchenlink.co.zw/) | — | 100 | oceanwp | astra-sites, creame-whatsapp-me, elementor +4 |

### Ngo — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [projectvote263.org.zw](https://projectvote263.org.zw/) | — | 100 | twentytwentyfive | all-in-one-seo-pack, elementor, elementor-pro +6 |
| [sapes.org.zw](https://sapes.org.zw/) | — | 100 | use | cookieadmin, cookieadmin-pro, download-manager +1 |
| [nprc.org.zw](https://nprc.org.zw/) | — | 100 | shoppystore | contact-form-7, content-views-query-and-display-post-page, easy-twitter-feed-widget +8 |
| [sai.org.zw](https://sai.org.zw/) | — | 93 | gutener-charity-ngo | contact-form-7, cookieadmin, elementor +12 |
| [psh.org.zw](https://psh.org.zw/) | — | 100 | organic-farm | contact-form-7, elementor, elementor-pro +5 |
| [igniteyouth.co.zw](https://igniteyouth.co.zw/) | — | 98 | oxpins | contact-form-7, elementor, elementskit-lite +4 |
| [talia.org.zw](https://talia.org.zw/) | — | 100 | hello-elementor | custom-footer-generator, ele-custom-skin, elementor +7 |
| [acfezimbabwe.co.zw](https://acfezimbabwe.co.zw/) | — | 100 | popularfx | newsletter, pagelayer, ultimate-member |
| [shekinahglory.org.zw](https://shekinahglory.org.zw/) | — | 100 | astra | elementor, elementskit-lite, gutenkit-blocks-addon +2 |
| [thisabilityhub.org.zw](https://thisabilityhub.org.zw/) | — | 100 | listinghive | astra-sites, astra-widgets, bb-header-footer +10 |

### Other — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [soccer24.co.zw](https://soccer24.co.zw/) | 508799 | 100 | s24-theme-2024 | google-analytics-for-wordpress, soccer24-banners-v1, tablepress |
| [applynow.co.zw](https://applynow.co.zw/) | 706154 | 93 | foxiz | contact-form-7, elementor, foxiz-core +2 |
| [vacancybox.co.zw](https://vacancybox.co.zw/) | 834485 | 100 | astra | wp-job-manager |
| [zimbamusic.co.zw](https://zimbamusic.co.zw/) | 841054 | 100 | Newspaper | download-monitor, rocket-lazy-load, td-cloud-library +2 |
| [ppaz.org.zw](https://ppaz.org.zw/) | — | 93 | — | maintenance |
| [petra.org.zw](https://petra.org.zw/) | — | 100 | Divi | bbpress, google-analytics-for-wordpress, learndash-course-grid +2 |
| [mopse.gov.zw](https://mopse.gov.zw/) | — | 100 | astra | 3d-flipbook-dflip-lite, astra-sites, elementor +6 |
| [aazimbabwe.co.zw](https://aazimbabwe.co.zw/) | — | 100 | hello-elementor | download-manager, elementor, elementor-pro +2 |
| [medpride.co.zw](https://medpride.co.zw/) | — | 100 | oceanwp | elementor, ocean-extra |
| [backasable.co.zw](https://backasable.co.zw/) | — | 100 | hello-elementor | Paynow-for-WooCommerce-master, elementor, jeg-elementor-kit +4 |

### Government — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [nacz.co.zw](https://nacz.co.zw/) | — | 100 | hello-elementor | contact-form-7, elementor, elementor-pro +9 |
| [auditorgeneral.gov.zw](https://auditorgeneral.gov.zw/) | — | 100 | corpiva | bit-assist, desert-companion, elementor +3 |
| [chipingerdc.gov.zw](https://chipingerdc.gov.zw/) | — | 100 | astra | 3d-flipbook-dflip-lite, astra-sites, elementor +1 |
| [ntbrl.org.zw](https://ntbrl.org.zw/) | — | 100 | hello-elementor | bdthemes-element-pack-lite, bdthemes-prime-slider-lite, download-manager +8 |
| [mashwest.gov.zw](https://mashwest.gov.zw/) | — | 98 | consultio | booked, case-theme-core, case-theme-user +14 |
| [moysar.gov.zw](https://moysar.gov.zw/) | — | 98 | oceanwp | 3d-flipbook-dflip-lite, elementor, ocean-extra +2 |
| [agric.gov.zw](https://agric.gov.zw/) | — | 100 | egovt | 3d-flipbook-dflip-lite, ameliabooking, animate-it +23 |
| [opcbyometro.gov.zw](https://opcbyometro.gov.zw/) | — | 100 | consultio | booked, case-theme-core, case-theme-user +10 |
| [opcmeal.gov.zw](https://opcmeal.gov.zw/) | — | 100 | spexo | 3d-flipbook-dflip-lite, betterdocs, betterlinks +20 |
| [opcmatnorth.gov.zw](https://opcmatnorth.gov.zw/) | — | 98 | astra | elementor, wpforms-lite |

### News — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [techzim.co.zw](https://techzim.co.zw/) | 271901 | 100 | afrispark-twenty25-child | google-site-kit, optimization-detective, shop +4 |
| [myzimbabwe.co.zw](https://myzimbabwe.co.zw/) | 391225 | 100 | Newspaper | advanced-ads, cleantalk-spam-protect, cookie-law-info +11 |
| [dailynews.co.zw](https://dailynews.co.zw/) | 463008 | 100 | inhype | Archive, adrotate, ajax-login-and-registration-modal-popup +9 |
| [vicfallslive.co.zw](https://vicfallslive.co.zw/) | — | 100 | zox-news | theia-post-slider-premium, theia-sticky-sidebar, zox-alp |
| [zifmstereo.co.zw](https://zifmstereo.co.zw/) | — | 90 | jannah | adrotate, animate-it, contact-form-7 +9 |
| [africahotspot.co.zw](https://africahotspot.co.zw/) | — | 100 | draftly |  |
| [radiozim.co.zw](https://radiozim.co.zw/) | — | 100 | proradio | contact-form-7, elementor, icons2go +10 |
| [thezimbabwetimes.co.zw](https://thezimbabwetimes.co.zw/) | — | 78 | soledad | contact-form-7, elementor, jetpack +5 |
| [zimetro.co.zw](https://zimetro.co.zw/) | — | 100 | — |  |
| [agriculture.co.zw](https://agriculture.co.zw/) | — | 100 | — |  |

### Blog — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [cdr.co.zw](https://cdr.co.zw/) | — | 100 | Divi | xagio-seo |
| [neguschronicles.co.zw](https://neguschronicles.co.zw/) | — | 100 | smart-mag | cleantalk-spam-protect, elementor, responsivevoice-text-to-speech |
| [p31woman.co.zw](https://p31woman.co.zw/) | — | 100 | — |  |
| [zimbabwehot100.co.zw](https://zimbabwehot100.co.zw/) | — | 100 | — |  |
| [afrobloggers.org.zw](https://afrobloggers.org.zw/) | — | 93 | — |  |
| [4thecity.org.zw](https://4thecity.org.zw/) | — | 100 | — |  |
| [wakandasolar.co.zw](https://wakandasolar.co.zw/) | — | 100 | — |  |
| [qutanga.co.zw](https://qutanga.co.zw/) | — | 88 | — |  |
| [diy.co.zw](https://diy.co.zw/) | — | 100 | — |  |
| [cellservices.co.zw](https://cellservices.co.zw/) | — | 100 | — |  |

### Religious — top 10

| Domain | Tranco rank | Score | Theme | Plugins |
| --- | --- | --- | --- | --- |
| [1c1031.co.zw](https://1c1031.co.zw/) | — | 100 | twentysixteen |  |
| [ccap.co.zw](https://ccap.co.zw/) | — | 100 | astra | astra-addon, bb-plugin, download-monitor |
| [domccp.co.zw](https://domccp.co.zw/) | — | 100 | — |  |
| [catholicdioceseofgweru.co.zw](https://catholicdioceseofgweru.co.zw/) | — | 100 | — |  |
| [basechurch.org.zw](https://basechurch.org.zw/) | — | 100 | — |  |
| [archedusechre.ac.zw](https://archedusechre.ac.zw/) | — | 100 | — |  |
| [andersonadventist.ac.zw](https://andersonadventist.ac.zw/) | — | 100 | — |  |
| [tacc.co.zw](https://tacc.co.zw/) | — | 100 | — |  |
| [ccjphre.org.zw](https://ccjphre.org.zw/) | — | 100 | — |  |
| [zimgospelmasters.co.zw](https://zimgospelmasters.co.zw/) | — | 100 | — |  |

## Plugin frequency (top 25)

| Plugin | Sites |
| --- | --- |
| elementor | 108 |
| contact-form-7 | 67 |
| woocommerce | 51 |
| revslider | 51 |
| elementor-pro | 42 |
| elementskit-lite | 28 |
| js_composer | 23 |
| download-manager | 23 |
| essential-addons-for-elementor-lite | 21 |
| header-footer-elementor | 18 |
| instagram-feed | 15 |
| wpforms-lite | 13 |
| google-analytics-for-wordpress | 11 |
| jetpack | 11 |
| 3d-flipbook-dflip-lite | 11 |
| click-to-chat-for-whatsapp | 11 |
| astra-sites | 10 |
| creame-whatsapp-me | 10 |
| wp-whatsapp-chat | 10 |
| yith-woocommerce-wishlist | 10 |
| google-site-kit | 9 |
| booked | 9 |
| case-theme-core | 9 |
| case-theme-user | 9 |
| mailchimp-for-wp | 8 |

## Theme frequency (top 15)

| Theme | Sites |
| --- | --- |
| hello-elementor | 22 |
| astra | 22 |
| Divi | 13 |
| popularfx | 5 |
| consultio | 5 |
| Avada | 4 |
| oceanwp | 4 |
| dt-the7 | 3 |
| hello-elementor-child | 3 |
| Newspaper | 2 |
| enfold | 2 |
| twentytwentyfive | 2 |
| blocksy | 2 |
| egovt | 2 |
| soledad | 2 |
