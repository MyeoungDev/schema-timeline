-- 컬럼 이름 변경은 Prisma 가 DROP/ADD 로 만들기 때문에 손으로 고친 마이그레이션이다.
ALTER TABLE "Post" RENAME COLUMN "authorId" TO "writerId";
ALTER TABLE "Post" RENAME CONSTRAINT "Post_authorId_fkey" TO "Post_writerId_fkey";

-- AlterTable
ALTER TABLE "Post" ALTER COLUMN "title" TYPE TEXT;
ALTER TABLE "Post" ADD COLUMN "publishedAt" TIMESTAMP(3);

-- DropColumn (data loss)
ALTER TABLE "User" DROP COLUMN "name";
