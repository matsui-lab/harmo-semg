#!/usr/bin/env Rscript
# Redraw retained results and add explicitly post hoc participant-bootstrap intervals. This script does not change manuscript selection.
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(grid)})
root <- normalizePath(file.path(dirname(sub('^--file=', '', grep('^--file=',commandArgs(),value=TRUE))), '../..'))
stem <- 'figure3_integrated_results_v008_2026.09.16-r8'
out <- file.path(root,'paper/figures/candidates',stem)
dir.create(out,recursive=TRUE,showWarnings=FALSE)
paths <- c('paper/results/validation/mapping_outcomes.csv',
 'paper/results/validation/semantic_coverage_comparison.json',
 'paper/results/validation/format_fidelity.json',
 'paper/results/additional_framework_v001_2026.09.12-r1/run4/T3_summary.json',
 'paper/results/additional_framework_v001_2026.09.12-r1/run4/T3_support_comparison.json',
 'paper/results/validation/physiology_positive_control.json',
 'paper/results/centroid_bootstrap_v001_2026.09.16-r1/summary.json')
j <- function(i) fromJSON(file.path(root,paths[i]),simplifyVector=FALSE)
mapping <- read.csv(file.path(root,paths[1])); cov <- j(2); fid <- j(3)
t3 <- j(4); comparisons <- j(5); phy <- j(6)
ids <- c('study31','study_28','study_32','study_33','study_38','study_43','study_120','study_1')
labs <- c('Controlled speed (31)','Lifespan / stroke (28)','HD-EMG + IMU (32)','Locomotion (33)',
 'Walking / stairs (38)','Sit-to-walk (43)','Gait120 (120)','Ramp walking (1)*')
formats <- c('c3d','mat','txt','csv','trc','mot')
mapping$format <- tolower(tools::file_ext(mapping$logical_source_member))
stopifnot(nrow(mapping)==78,all(mapping$outcome=='converted'),all(mapping$format %in% formats))
a <- expand.grid(study=ids,format=formats,stringsAsFactors=FALSE)
a$n <- mapply(function(s,f)sum(mapping$study_id==s & mapping$format==f),a$study,a$format)
a$x <- 48+(match(a$format,formats)-1)*7.6; a$y <- 43-(match(a$study,ids)-1)*4.25
b <- do.call(rbind,lapply(cov$study_rows,function(r)data.frame(study=r$study_id,before=length(r$file_only_properties),after=length(r$paper_assisted_properties))))
b <- b[match(ids,b$study),]; b$y <- 43-(0:7)*4.25
stopifnot(sum(b$before)==24,sum(b$after)==43)
f <- do.call(rbind,lapply(fid$comparisons,function(r)data.frame(group=if(r$candidate_format=='mat')'ASCII / MAT' else 'C3D / alternatives',outcome=r$observed_outcome)))
oc <- c('equivalent','non-equivalent','source-absent-retained')
c <- expand.grid(outcome=oc,group=c('ASCII / MAT','C3D / alternatives'),stringsAsFactors=FALSE)
c$n <- mapply(function(g,o)sum(f$group==g & f$outcome==o),c$group,c$outcome)
stopifnot(identical(c$n,c(30L,0L,2L,26L,34L,4L)))
c$end <- ave(c$n,c$group,FUN=cumsum); c$start <- c$end-c$n
c$y <- ifelse(c$group=='ASCII / MAT',43,26)
conditions <- c('retained','withdrawn','restored'); uses <- c('activation-timing','amplitude-pooling')
d <- do.call(rbind,lapply(uses,function(u)do.call(rbind,lapply(conditions,function(s){
 rows <- Filter(function(r)r$condition==s && r$decision$intended_use==u,comparisons)
 status <- unique(vapply(rows,function(r)r$detail$assertion$status,character(1)))
 release <- unique(vapply(rows,function(r)r$decision$status,character(1)))
 stopifnot(length(rows)==20,length(status)==1,length(release)==1,
   all(vapply(rows,function(r)isTRUE(r$sample_and_time_reference_unchanged),logical(1))))
 stopifnot(t3$outcome_counts[[paste(u,s,status,sep=':')]]==20,
   t3$release_counts[[paste(u,s,release,sep=':')]]==20)
 data.frame(purpose=u,condition=s,status=status,release=release,n=length(rows))
}))))
stopifnot(t3$all_passed,t3$evaluated_pairs==20,t3$evaluations==120,
 t3$exact_restorations==40,t3$exact_restoration_comparisons==40,
 identical(d$status,c('comparable','conditionally_comparable','comparable',rep('unassessed',3))),
 identical(d$release,c('allow','deny','allow',rep('quarantine',3))))
e <- do.call(rbind,lapply(phy$metrics,function(r)data.frame(study=r$study_id,subject=r$subject_id,muscle=r$muscle,centroid=r$activation_centroid_percent_gait_cycle,pass=r$pattern_pass)))
stopifnot(nrow(e)==40,sum(!e$pass)==2,!phy$amplitude_pooled_across_studies)
blue <- '#A8CADB'; teal <- '#A8CBBE'; ochre <- '#EDCAA5'; grey <- '#D5D9DF'; ink <- '#111111'; rule <- '#9BA3A8'
pt <- 72.27/25.4
txt <- function(x,y,label,size=7.5,bold=FALSE,hjust=0) annotate('text',x=x,y=y,label=label,size=size/pt,family='Arial',fontface=if(bold)'bold' else 'plain',colour=ink,hjust=hjust,vjust=.5,lineheight=1.05)
seg <- function(x,y,xe,ye=y,col=rule,lw=.2) annotate('segment',x=x,y=y,xend=xe,yend=ye,colour=col,linewidth=lw)
can <- function(w,h) ggplot()+coord_cartesian(xlim=c(0,w),ylim=c(0,h),expand=FALSE,clip='off')+theme_void()+theme(plot.margin=margin(0,0,0,0),plot.background=element_rect(fill='white',colour=NA))
title <- function(l,t,y)list(txt(2,y,l,11,TRUE),txt(9,y,t,9,TRUE))
pA <- can(95,62)+title('A','Conversion coverage',58)+txt(9,51,'Public study')+
 txt(48+(0:5)*7.6,51,toupper(formats),7,hjust=.5)+txt(9,a$y[1:8],labs,7)+
 geom_point(data=subset(a,n>0),aes(x,y),shape=21,fill=blue,colour=ink,stroke=.2,size=1.8)+
 txt(9,4,'78 / 78 registered settings converted',8,TRUE)
pB <- can(95,62)+title('B','Contribution of literature',58)+txt(9,51,'Study')+
 annotate('point',x=c(33,58),y=51,shape=c(21,16),fill='white',colour=c(rule,blue),size=1.7)+
 txt(c(36,61),51,c('Files','+ Literature'),7)+txt(9,b$y,c('31','28','32','33','38','43','120','1*'),7)+
 geom_segment(data=b,aes(x=27+before*7,xend=27+after*7,y=y,yend=y),colour=grey,linewidth=.55)+
 geom_point(data=b,aes(x=27+before*7,y=y),shape=21,fill='white',colour=rule,size=1.8,stroke=.35)+
 geom_point(data=b,aes(x=27+after*7,y=y),colour=blue,size=1.8)+seg(27,10,83)+
 txt(27+(0:4)*14,7,seq(0,8,2),7,hjust=.5)+txt(9,1.8,'24 to 43 study-property pairs',8,TRUE)
z <- subset(c,n>0)
pC <- can(70,70)+title('C','Agreement across formats',65)+txt(9,58,'Study 31: representation pairs',7)+
 txt(9,c(51,34),c('ASCII / MAT','C3D / alternatives'),7)+
 geom_rect(data=z,aes(xmin=9+start*.86,xmax=9+end*.86,ymin=y-1.6,ymax=y+1.6,fill=outcome),colour='white',linewidth=.2)+
 scale_fill_manual(values=setNames(c(blue,ochre,grey),oc),guide='none')+
 txt(9+(z$start+z$end)*.43,z$y+3.7,z$n,7,hjust=.5)+
 annotate('point',x=c(10,10,10),y=c(17,11,5),shape=15,colour=c(blue,ochre,grey),size=1.7)+
 txt(14,c(17,11,5),c('Equivalent','Discordant','Source absent'),7)
d$x <- 44+(match(d$condition,conditions)-1)*29
d$y <- ifelse(d$purpose=='activation-timing',34,16)
d$label <- c('Comparable','Conditional','Comparable',rep('Unassessed',3))
d$release_label <- c('Release allowed','Release denied','Release allowed',rep('Output held',3))
# Conventional result table. Count permission, not matches or accuracy.
allowed <- ifelse(d$release=='allow',d$n,0L)
stopifnot(identical(as.integer(allowed),c(20L,0L,20L,0L,0L,0L)))
table_values <- list(
 c('Purpose','Information\navailable','Information\nremoved','Information\nrestored'),
 c('Timing comparison',sprintf('%d / %d',allowed[1:3],d$n[1:3])),
 c('Amplitude pooling',sprintf('%d / %d',allowed[4:6],d$n[4:6])))
write_json(table_values,file.path(out,paste0(stem,'_D_table.json')),pretty=TRUE)
pD <- can(120,70)+title('D','Processing information and output permission',65)+
 txt(9,57,'Pairs with output permitted / pairs assessed',7.5)+
 seg(c(9,43,67,91,115),51,c(9,43,67,91,115),21)+
 seg(9,c(51,41,31,21),115)+
 txt(10,c(46,36,26),vapply(table_values,function(r)r[1],character(1)),7.5)+
 txt(c(55,79,103),46,table_values[[1]][2:4],7.5,TRUE,hjust=.5)+
 txt(c(55,79,103),36,table_values[[2]][2:4],7.5,hjust=.5)+
 txt(c(55,79,103),26,table_values[[3]][2:4],7.5,hjust=.5)+
 txt(9,13,'Same 20 waveform pairs throughout. Only processing information changed.',6.8)+
 txt(9,7,'0 / 20 indicates withheld output, not waveform disagreement.',6.8)
# Display spread never changes centroid values. Include both pattern failures.
e$mi <- match(e$muscle,c('tibialis anterior','medial gastrocnemius')); e$si <- match(e$study,c('study_38','study_120'))
e$spread <- ave(seq_len(nrow(e)),interaction(e$muscle,e$study),FUN=function(v) seq(-3,3,length.out=length(v)))
e$x <- ifelse(e$mi==1,50,128)+(e$si-1)*33+e$spread; e$y <- 45+(e$centroid-25)*1.07
med <- aggregate(centroid~study+muscle,data=e,median)
med$x <- ifelse(med$muscle=='tibialis anterior',50,128)+ifelse(med$study=='study_38',0,33)
med$y <-45+(med$centroid-25)*1.07
bootstrap <- j(7)
stopifnot(bootstrap$status=='post_hoc_additional_analysis',bootstrap$resamples==100000,
 bootstrap$seed==20260916,bootstrap$n_per_study==10,bootstrap$interval_method=='percentile')
difference <- do.call(rbind,lapply(bootstrap$estimates,function(z)
 data.frame(muscle=z$muscle,signed_median_difference_pp=z$signed_median_difference_pp,
 ci_lower_pp=z$ci_lower_pp,ci_upper_pp=z$ci_upper_pp,
 study38_median=z$study38_median,gait120_median=z$gait120_median)))
difference$y <- c(20,12)
delta <- difference$signed_median_difference_pp
for(i in 1:2){
 m <- difference$muscle[i]
 observed <- med$centroid[med$muscle==m & med$study=='study_120']-
             med$centroid[med$muscle==m & med$study=='study_38']
 stopifnot(abs(observed-delta[i])<1e-10,difference$ci_lower_pp[i]<delta[i],
           difference$ci_upper_pp[i]>delta[i])
}
stopifnot(identical(round(unname(delta),2),c(.52,1.17)))
write.csv(difference,file.path(out,paste0(stem,'_E_median_differences.csv')),row.names=FALSE)
pE <- can(190,106)+title('E','Physiological timing',102)+
 txt(c(66.5,144.5),94,c('Tibialis anterior','Medial gastrocnemius'),8,TRUE,hjust=.5)+
 txt(9,90,'Centroid (% gait cycle)',7)+seg(19,45,19,87.8)+
 txt(17,45+(seq(25,65,10)-25)*1.07,seq(25,65,10),7,hjust=1)+
 geom_segment(data=med,aes(x=x-5,xend=x+5,y=y,yend=y),colour=ink,linewidth=.35)+
 geom_point(data=e[order(!e$pass),],aes(x,y,fill=ifelse(pass,study,'Pattern not met')),shape=21,colour=ink,stroke=.25,size=2)+
 scale_fill_manual(values=c(study_38=blue,study_120=teal,'Pattern not met'=ochre),guide='none')+
 txt(c(50,83,128,161),41,c('Study 38','Gait120','Study 38','Gait120'),7,hjust=.5)+
 annotate('point',x=24,y=33,shape=21,fill=ochre,colour=ink,stroke=.25,size=1.7)+
 txt(28,33,'Pattern not met (2 / 40 muscle assessments)',7)+
 txt(9,29,'Median difference with 95% bootstrap CI',8,TRUE)+
 txt(9,c(20,12),c('Tibialis anterior','Medial gastrocnemius'),7)+
 # Dashed zero line is a reference for direction, not an acceptance threshold.
 seg(120,seq(9,23,2),120,seq(9,23,2)+1,col=rule,lw=.25)+
 geom_segment(data=difference,aes(x=120+ci_lower_pp*7.5,xend=120+ci_upper_pp*7.5,
     y=y,yend=y),colour=ink,linewidth=.35)+
 geom_segment(data=difference,aes(x=120+ci_lower_pp*7.5,xend=120+ci_lower_pp*7.5,
     y=y-1,yend=y+1),colour=ink,linewidth=.3)+
 geom_segment(data=difference,aes(x=120+ci_upper_pp*7.5,xend=120+ci_upper_pp*7.5,
     y=y-1,yend=y+1),colour=ink,linewidth=.3)+
 geom_point(data=difference,aes(x=120+signed_median_difference_pp*7.5,y=y),
     shape=21,fill=blue,colour=ink,stroke=.25,size=2)+
 txt(120+delta*7.5,difference$y+3.2,sprintf('%+.2f',delta),7.5,TRUE,hjust=.5)+
 seg(60,8,180)+seg(120+seq(-8,8,4)*7.5,8,120+seq(-8,8,4)*7.5,7.3)+
 txt(120+seq(-8,8,4)*7.5,5.2,c('-8','-4','0','4','8'),7,hjust=.5)+
 txt(120,1.5,'Gait120 - Study 38 (percentage points of gait cycle)',7,hjust=.5)
draw <- function(){
 grid.newpage();pushViewport(viewport(layout=grid.layout(3,1,heights=unit(c(62,70,106),'mm'))))
 for(i in 1:2){
  widths <- list(c(95,95),c(70,120))[[i]]
  panels <- list(list(pA,pB),list(pC,pD))[[i]]
  pushViewport(viewport(layout.pos.row=i,layout=grid.layout(1,2,widths=unit(widths,'mm'))))
  for(k in 1:2) print(panels[[k]],newpage=FALSE,vp=viewport(layout.pos.col=k))
  popViewport()
 }
 print(pE,newpage=FALSE,vp=viewport(layout.pos.row=3));popViewport()
}
grDevices::quartz(type='pdf',file=file.path(out,paste0(stem,'.pdf')),width=190/25.4,height=238/25.4,family='Arial');draw();invisible(dev.off())
svglite::svglite(file.path(out,paste0(stem,'.svg')),width=190/25.4,height=238/25.4);draw();invisible(dev.off())
ragg::agg_png(file.path(out,paste0(stem,'.png')),width=190,height=238,units='mm',res=300);draw();invisible(dev.off())
for(n in c('a','b','c','d','e'))write.csv(get(n),file.path(out,paste0(stem,'_',toupper(n),'_data.csv')),row.names=FALSE)
manifest <- list(status='candidate_not_adopted',version='v008_2026.09.16-r8',figure=3,
 graphic_engine=paste('ggplot2',packageVersion('ggplot2')),width_mm=190,height_mm=238,
 checks=list(converted=78,study_property_before=24,study_property_after=43,format_counts=c$n,
  fixed_pairs=20,assessments=120,exact_restorations=40,physiology_points=nrow(e),pattern_not_met=sum(!e$pass),median_differences_pp=unname(delta),bootstrap_resamples=bootstrap$resamples,bootstrap_seed=bootstrap$seed,panels=LETTERS[1:5]),
 boundaries=list(separate_evaluations=TRUE,new_experiments=FALSE,additional_post_hoc_analysis=TRUE,manuscript_changed=FALSE,
  t3=t3$claim_boundary,amplitude_pooling_established=FALSE),
 sources=lapply(paths,function(p)list(path=p,sha256=digest::digest(file=file.path(root,p),algo='sha256'))))
write_json(manifest,file.path(out,paste0(stem,'_evidence.json')),pretty=TRUE,auto_unbox=TRUE)
cat(out,'\n')
